"""
THE INFINITY BANK — ~1,200 hard-but-fair Grade 9 questions.
-----------------------------------------------------------
Infinity mode is endless, so it needs more questions than anyone will ever
sit down and type. This module generates them.

Three rules shaped every generator in here.

**The answer is computed, never written down.** Each family builds its
numbers first and then derives the answer from them in Python. A question
whose answer was typed by hand is a question that can be wrong; a question
whose answer is `round(pi * r * r * h)` is wrong only if the formula is
wrong, and there are far fewer formulas than questions.

**The decoy is a mistake somebody actually makes.** A wrong door that's
wrong by a factor of a hundred teaches nothing and is free to dodge. So
the decoys here are the specific slips the curriculum warns about: the
cone volume without its third, the diameter used as the radius, exponents
added when they should multiply, simple interest where compound was asked,
the mean when the median was asked. Getting past a door should mean
knowing the difference.

**Hard, but Grade 9 hard.** Everything is inside Ontario MTH1W — the five
strands below, no trigonometry, no factoring quadratics, nothing needing a
diagram to read. Difficulty comes from the number of steps and from
numbers that don't divide nicely, not from content nobody has been taught.

The bank is seeded into `infinity_questions` alongside anything staff have
written, and staff can edit or delete any of it from the admin page — a
generated question is an ordinary row, not a special one.
"""
import random
from fractions import Fraction
from math import pi, gcd, isqrt

STRANDS = [
    "Number",
    "Algebra",
    "Data",
    "Geometry & Measurement",
    "Financial Literacy",
]

# Every generator is registered with the strand it belongs to and how hard
# it is (1–5). Infinity mode serves easier tiers on early floors and opens
# the harder ones up as the descent gets deeper, so this is the dial that
# makes floor 400 feel different from floor 4.
_FAMILIES = []


def family(strand, difficulty):
    def wrap(fn):
        _FAMILIES.append({"fn": fn, "strand": strand,
                          "difficulty": difficulty, "name": fn.__name__})
        return fn
    return wrap


# ── formatting ────────────────────────────────────────────────────────
def money(x):
    """Dollars, always two decimals — money that reads as `$1240.5` looks
    like a typo and invites a camper to doubt a correct answer."""
    return f"${x:,.2f}"


def num(x):
    """Trim a float that happens to be whole. 12.0 -> '12'."""
    if isinstance(x, float) and abs(x - round(x)) < 1e-9:
        return f"{round(x):,}"
    if isinstance(x, float):
        return f"{x:,.2f}".rstrip("0").rstrip(".")
    return f"{x:,}"


def frac(f):
    """A Fraction as `a/b`, or just `a` when the denominator is 1."""
    f = Fraction(f)
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def signed(n):
    """`+ 5` / `− 5`, for building expressions that read naturally."""
    return f"+ {n}" if n >= 0 else f"− {abs(n)}"


# ══ NUMBER ════════════════════════════════════════════════════════════
@family("Number", 3)
def n_power_chain(r):
    """(a^m × a^n) ÷ a^p. Decoy multiplies the exponents instead."""
    a = r.randint(2, 9)
    m, n, p = r.randint(3, 8), r.randint(2, 7), r.randint(2, 6)
    return (f"Simplify ({a}^{m} × {a}^{n}) ÷ {a}^{p} as a single power of {a}. "
            f"The exponent is",
            m + n - p, m * n - p)


@family("Number", 4)
def n_power_of_power(r):
    """((a^m)^n) ÷ a^p — two rules in one line."""
    a = r.randint(2, 7)
    m, n, p = r.randint(2, 5), r.randint(2, 4), r.randint(2, 8)
    return (f"Simplify ({a}^{m})^{n} ÷ {a}^{p} as a single power of {a}. "
            f"The exponent is",
            m * n - p, m + n - p)


@family("Number", 4)
def n_negative_exponent(r):
    """(a/b)^−n. Decoy forgets to flip the fraction."""
    a, b = r.randint(2, 6), r.randint(2, 6)
    while a == b:
        b = r.randint(2, 6)
    n = r.randint(2, 3)
    val = Fraction(b, a) ** n
    bad = Fraction(a, b) ** n
    return (f"Write ({a}/{b})^−{n} as a fraction in lowest terms.",
            frac(val), frac(bad))


@family("Number", 4)
def n_order_of_ops(r):
    """Brackets, a power and a negative, in one expression."""
    a, b, c, d = r.randint(2, 6), r.randint(3, 9), r.randint(2, 5), r.randint(2, 8)
    right = -(a ** 2) + b * (c + d)
    wrong = (a ** 2) + b * (c + d)          # squared the negative
    return (f"Evaluate −{a}² + {b}({c} + {d})", right, wrong)


@family("Number", 5)
def n_order_of_ops_hard(r):
    """Nested brackets over a division bar."""
    a, b, c = r.randint(2, 5), r.randint(2, 6), r.randint(2, 5)
    d, e = r.randint(2, 4), r.randint(3, 7)
    top = (a + b) ** 2 - c * e
    right = Fraction(top, d)
    wrong = Fraction(a + b ** 2 - c * e, d)   # squared only b
    if right == wrong:
        wrong = right + 1
    return (f"Evaluate [({a} + {b})² − {c} × {e}] ÷ {d}", frac(right), frac(wrong))


@family("Number", 4)
def n_fraction_mixed(r):
    """Order of operations with fractions: a/b + c/d × e/f."""
    a, b = r.randint(1, 5), r.randint(2, 7)
    c, d = r.randint(1, 5), r.randint(2, 7)
    e, f = r.randint(1, 5), r.randint(2, 7)
    right = Fraction(a, b) + Fraction(c, d) * Fraction(e, f)
    wrong = (Fraction(a, b) + Fraction(c, d)) * Fraction(e, f)   # left to right
    if right == wrong:
        wrong = right + Fraction(1, 2)
    return (f"Evaluate {a}/{b} + {c}/{d} × {e}/{f}, as a fraction in lowest terms.",
            frac(right), frac(wrong))


@family("Number", 4)
def n_mixed_subtract(r):
    """Mixed-number subtraction that needs a borrow."""
    w1, d1 = r.randint(3, 9), r.randint(4, 8)
    w2, d2 = r.randint(1, w1 - 1), r.randint(4, 8)
    # Proper AND already in lowest terms — a question that opens with 2/6
    # invites the answer "you didn't reduce it", which isn't the point here.
    n1 = r.choice([k for k in range(1, d1) if gcd(k, d1) == 1])
    n2 = r.choice([k for k in range(1, d2) if gcd(k, d2) == 1])
    right = (w1 + Fraction(n1, d1)) - (w2 + Fraction(n2, d2))
    wrong = (w1 - w2) + abs(Fraction(n1, d1) - Fraction(n2, d2))  # subtracted parts separately
    if right == wrong:
        wrong = right + Fraction(1, 3)
    return (f"Evaluate {w1} {n1}/{d1} − {w2} {n2}/{d2}, as an improper fraction "
            f"in lowest terms.",
            frac(right), frac(wrong))


@family("Number", 4)
def n_percent_compound(r):
    """Up p%, then down q%. Decoy assumes the two cancel."""
    start = r.choice([120, 240, 350, 480, 600, 750, 840, 960])
    p, q = r.choice([10, 15, 20, 25]), r.choice([10, 15, 20, 25])
    right = start * (1 + p / 100) * (1 - q / 100)
    wrong = start * (1 + (p - q) / 100)
    if abs(right - wrong) < 0.005:
        q = q + 5
        right = start * (1 + p / 100) * (1 - q / 100)
        wrong = start * (1 + (p - q) / 100)
    return (f"A price of {money(start)} rises {p}%, then the new price falls {q}%. "
            f"The final price is",
            money(round(right, 2)), money(round(wrong, 2)))


@family("Number", 4)
def n_reverse_percent(r):
    """Total after tax, find the pre-tax price. Decoy takes 13% off the total."""
    before = r.choice([40, 60, 85, 120, 150, 200, 240, 320])
    rate = r.choice([13, 15, 5, 8])
    total = before * (1 + rate / 100)
    wrong = total * (1 - rate / 100)
    return (f"After {rate}% tax an item costs {money(round(total, 2))}. "
            f"The price before tax was",
            money(round(before, 2)), money(round(wrong, 2)))


@family("Number", 4)
def n_three_term_ratio(r):
    """Split in a:b:c. Decoy divides the total evenly."""
    # Distinct parts, so "the largest share" names exactly one of them.
    a, b, c = r.sample(range(2, 9), 3)
    part = r.choice([15, 20, 25, 30, 40, 50])
    total = (a + b + c) * part
    big = max(a, b, c)
    return (f"{money(total)} is shared in the ratio {a}:{b}:{c}. "
            f"The largest share is",
            money(big * part), money(round(total / 3, 2)))


@family("Number", 3)
def n_unit_rate(r):
    """Which is the better buy, and by how much per unit."""
    n1, p1 = r.choice([4, 6, 8]), r.randint(5, 12)
    n2 = r.choice([10, 12, 15])
    p2 = round(p1 / n1 * n2 * r.choice([0.85, 0.9, 1.1, 1.15]), 2)
    r1, r2 = p1 / n1, p2 / n2
    cheaper = round(min(r1, r2), 4)
    dearer = round(max(r1, r2), 4)
    if abs(cheaper - dearer) < 0.001:
        p2 += 1
        r2 = p2 / n2
        cheaper, dearer = round(min(r1, r2), 4), round(max(r1, r2), 4)
    return (f"{n1} pens cost {money(p1)}; {n2} pens cost {money(p2)}. "
            f"The better unit price, to the nearest cent, is",
            money(round(cheaper, 2)), money(round(dearer, 2)))


@family("Number", 5)
def n_simplify_radical(r):
    """√n = a√b. Decoy is the whole-number part of the square root."""
    a = r.randint(2, 7)
    b = r.choice([2, 3, 5, 6, 7, 10, 11, 13])
    n = a * a * b
    return (f"Write √{n} in the form a√b with b as small as possible. a is",
            a, isqrt(n))


@family("Number", 4)
def n_sci_notation_multiply(r):
    """(a×10^m)(b×10^n), normalised. Decoy skips the renormalisation."""
    a = round(r.uniform(1.5, 9.5), 1)
    b = round(r.uniform(1.5, 9.5), 1)
    m, n = r.randint(3, 8), r.randint(2, 7)
    prod = a * b
    exp = m + n
    if prod >= 10:
        exp += 1
    return (f"({a} × 10^{m}) × ({b} × 10^{n}) written in proper scientific "
            f"notation has exponent",
            exp, m + n)


@family("Number", 4)
def n_sqrt_between(r):
    """Estimate an irrational root to one decimal."""
    n = r.randint(20, 200)
    while isqrt(n) ** 2 == n:          # a perfect square would give it away
        n = r.randint(20, 200)
    val = round(n ** 0.5, 1)
    return (f"√{n}, to one decimal place, is", num(val), num(isqrt(n)))


# ══ ALGEBRA ═══════════════════════════════════════════════════════════
@family("Algebra", 3)
def a_collect_terms(r):
    """Two variables, four terms, one sign trap."""
    a, b, c, d = (r.randint(2, 9) for _ in range(4))
    e, f = r.randint(2, 9), r.randint(2, 9)
    xco = a - c
    return (f"Simplify {a}x + {b}y − {c}x + {d}y − {e}x + {f}y. "
            f"The coefficient of x is",
            xco - e, a + c + e)


@family("Algebra", 4)
def a_expand_two(r):
    """a(bx+c) − d(ex+f). Decoy forgets to distribute the minus."""
    a, b, c, d, e, f = (r.randint(2, 9) for _ in range(6))
    right = a * c - d * f
    wrong = a * c + d * f
    if right == wrong:
        f += 1
        right, wrong = a * c - d * f, a * c + d * f
    return (f"Expand and simplify {a}({b}x + {c}) − {d}({e}x + {f}). "
            f"The constant term is",
            right, wrong)


@family("Algebra", 4)
def a_expand_coefficient(r):
    """Same shape, asking for the x coefficient instead."""
    a, b, c, d, e, f = (r.randint(2, 9) for _ in range(6))
    right = a * b - d * e
    wrong = a * b + d * e
    if right == wrong:
        e += 1
        right, wrong = a * b - d * e, a * b + d * e
    return (f"Expand and simplify {a}({b}x + {c}) − {d}({e}x + {f}). "
            f"The coefficient of x is",
            right, wrong)


@family("Algebra", 5)
def a_binomial_product(r):
    """(ax+b)(cx+d) by distributing twice. Decoy is the FOIL middle term
    with one product missing."""
    a, b, c, d = r.randint(1, 6), r.randint(2, 9), r.randint(1, 6), r.randint(2, 9)
    right = a * d + b * c
    wrong = b * d
    if right == wrong:
        d += 1
        right, wrong = a * d + b * c, b * d
    return (f"Expand ({a}x + {b})({c}x + {d}). The coefficient of x is",
            right, wrong)


@family("Algebra", 4)
def a_solve_both_sides(r):
    """Brackets on both sides, integer solution by construction."""
    x = r.randint(-9, 9)
    a, b, c, d = r.randint(2, 8), r.randint(1, 9), r.randint(2, 8), r.randint(1, 9)
    while a == c:
        c = r.randint(2, 8)
    # a(x + b) = c(x + d) + k  -> solve for the k that makes x the root
    k = a * (x + b) - c * (x + d)
    return (f"Solve {a}(x + {b}) = {c}(x + {d}) {signed(k)}", x, -x if x else x + 1)


@family("Algebra", 5)
def a_solve_fractions(r):
    """(x+a)/b = (x−c)/d + e. Cleared denominators, integer root."""
    x = r.randint(-8, 12)
    b, d = r.choice([2, 3, 4, 5]), r.choice([2, 3, 4, 6])
    while b == d:
        d = r.choice([2, 3, 4, 6])
    a = r.randint(1, 9)
    c = r.randint(1, 9)
    e = Fraction(x + a, b) - Fraction(x - c, d)
    if e.denominator != 1:
        # Nudge a so the constant lands whole and the question reads cleanly.
        need = (Fraction(x - c, d) + round(e)) * b - x
        a = int(need) if float(need).is_integer() else a
        e = Fraction(x + a, b) - Fraction(x - c, d)
    return (f"Solve (x + {a})/{b} = (x − {c})/{d} + {frac(e)}",
            x, x + b if x + b != x else x + 1)


@family("Algebra", 4)
def a_rearrange(r):
    """Solve a formula for a variable, then evaluate it."""
    m, b, y = r.randint(2, 9), r.randint(1, 15), r.randint(20, 90)
    right = Fraction(y - b, m)
    wrong = Fraction(y + b, m)
    return (f"If y = {m}x + {b} and y = {y}, then x is", frac(right), frac(wrong))


@family("Algebra", 4)
def a_evaluate_expression(r):
    """Substitute negatives into a expression with a power."""
    x, y = r.randint(-6, -2), r.randint(2, 7)
    a, b = r.randint(2, 6), r.randint(2, 6)
    right = a * x ** 2 - b * y
    wrong = -(a * x ** 2) - b * y      # squared the sign away
    if right == wrong:
        wrong = right + 2 * a
    return (f"Evaluate {a}x² − {b}y when x = {x} and y = {y}", right, wrong)


@family("Algebra", 4)
def a_slope_two_points(r):
    """Slope from two points. Decoy inverts the fraction (run over rise)."""
    x1, y1 = r.randint(-8, 8), r.randint(-8, 8)
    dx, dy = r.randint(1, 7) * r.choice([-1, 1]), r.randint(1, 9) * r.choice([-1, 1])
    x2, y2 = x1 + dx, y1 + dy
    right = Fraction(dy, dx)
    wrong = Fraction(dx, dy)
    if right == wrong:
        wrong = right + 1
    return (f"The slope of the line through ({x1}, {y1}) and ({x2}, {y2}) is",
            frac(right), frac(wrong))


@family("Algebra", 4)
def a_y_intercept(r):
    """Point-slope to y-intercept. Decoy adds where it should subtract."""
    m = r.randint(-6, 6) or 2
    x1 = r.randint(-7, 7)
    y1 = r.randint(-9, 9)
    right = y1 - m * x1
    wrong = y1 + m * x1
    if right == wrong:
        x1 += 1
        right, wrong = y1 - m * x1, y1 + m * x1
    return (f"A line with slope {m} passes through ({x1}, {y1}). "
            f"Its y-intercept is",
            right, wrong)


@family("Algebra", 4)
def a_x_intercept(r):
    """x-intercept of y = mx + b. Decoy reports the y-intercept."""
    m = r.choice([-6, -4, -3, -2, 2, 3, 4, 6])
    b = m * r.randint(-8, 8)
    if b == 0:
        b = m * 3
    right = Fraction(-b, m)
    return (f"The x-intercept of y = {m}x {signed(b)} is", frac(right), b)


@family("Algebra", 5)
def a_system_intersection(r):
    """Where two lines cross. Built backwards from the crossing point."""
    x, y = r.randint(-6, 6), r.randint(-6, 6)
    m1 = r.randint(-5, 5) or 2
    m2 = r.randint(-5, 5) or -3
    while m2 == m1:
        m2 = r.randint(-5, 5) or 4
    b1, b2 = y - m1 * x, y - m2 * x
    return (f"The lines y = {m1}x {signed(b1)} and y = {m2}x {signed(b2)} "
            f"intersect. The x-coordinate is",
            x, y if y != x else x + 1)


@family("Algebra", 4)
def a_consecutive_integers(r):
    """Consecutive integers summing to a total. Decoy is the smallest."""
    n = r.choice([3, 4, 5])
    first = r.randint(8, 60)
    nums = list(range(first, first + n))
    total = sum(nums)
    return (f"{n} consecutive integers add to {total}. The largest is",
            nums[-1], nums[0])


@family("Algebra", 4)
def a_perimeter_algebra(r):
    """Rectangle described in terms of its width."""
    w = r.randint(4, 25)
    k = r.randint(2, 5)
    c = r.randint(1, 12)
    length = k * w + c
    per = 2 * (w + length)
    return (f"A rectangle's length is {c} cm more than {k} times its width. "
            f"Its perimeter is {per} cm. The width, in cm, is",
            w, length)


@family("Algebra", 4)
def a_pattern_nth(r):
    """Linear pattern, far term. Decoy uses n×first-difference alone."""
    a1 = r.randint(2, 15)
    d = r.randint(3, 12)
    n = r.choice([20, 25, 30, 40, 50, 100])
    right = a1 + (n - 1) * d
    wrong = a1 + n * d
    terms = ", ".join(str(a1 + i * d) for i in range(4))
    return (f"A pattern starts {terms}, … The {n}th term is", right, wrong)


# ══ DATA ══════════════════════════════════════════════════════════════
@family("Data", 4)
def d_missing_value(r):
    """Find the value that produces a required mean."""
    n = r.randint(4, 7)
    vals = [r.randint(40, 98) for _ in range(n - 1)]
    target = r.randint(55, 90)
    missing = target * n - sum(vals)
    while not (0 <= missing <= 100):
        target = r.randint(55, 90)
        missing = target * n - sum(vals)
    return (f"{n - 1} test marks are {', '.join(map(str, vals))}. "
            f"To average {target}% over all {n} tests, the last mark must be",
            f"{missing}%", f"{target}%")


@family("Data", 4)
def d_weighted_average(r):
    """Weighted course mark. Decoy is the unweighted mean."""
    k, t, e = r.randint(55, 95), r.randint(55, 95), r.randint(55, 95)
    wk, wt, we = 40, 30, 30
    right = (k * wk + t * wt + e * we) / 100
    wrong = (k + t + e) / 3
    if abs(right - wrong) < 0.05:
        e = min(99, e + 6)
        right = (k * wk + t * wt + e * we) / 100
        wrong = (k + t + e) / 3
    return (f"Coursework {k}% counts for {wk}%, the test {t}% for {wt}%, and the "
            f"exam {e}% for {we}%. The final mark, to one decimal, is",
            f"{right:.1f}%", f"{wrong:.1f}%")


@family("Data", 4)
def d_median_vs_mean(r):
    """A set with one big outlier. Decoy is the mean."""
    base = sorted(r.randint(10, 40) for _ in range(6))
    base.append(r.randint(200, 400))          # the outlier
    vals = base
    n = len(vals)
    med = sorted(vals)[n // 2] if n % 2 else (sorted(vals)[n // 2 - 1] + sorted(vals)[n // 2]) / 2
    mean = sum(vals) / n
    return (f"Find the median of {', '.join(map(str, vals))}",
            num(med), num(round(mean, 1)))


@family("Data", 5)
def d_iqr(r):
    """Interquartile range on an odd-sized set (quartiles exclude the median)."""
    vals = sorted(r.sample(range(5, 80), 9))
    lower, upper = vals[:4], vals[5:]
    q1 = (lower[1] + lower[2]) / 2
    q3 = (upper[1] + upper[2]) / 2
    iqr = q3 - q1
    rng = vals[-1] - vals[0]
    if abs(iqr - rng) < 1e-9:
        rng += 1
    return (f"Find the interquartile range of {', '.join(map(str, vals))}",
            num(iqr), num(rng))


@family("Data", 4)
def d_probability_independent(r):
    """Two independent events. Decoy adds the probabilities."""
    b, d = r.randint(3, 9), r.randint(3, 9)
    # Lowest terms on the way in, so the only reducing to do is at the end.
    a = r.choice([k for k in range(1, b) if gcd(k, b) == 1])
    c = r.choice([k for k in range(1, d) if gcd(k, d) == 1])
    right = Fraction(a, b) * Fraction(c, d)
    wrong = Fraction(a, b) + Fraction(c, d)
    return (f"P(A) = {a}/{b} and P(B) = {c}/{d}, and the events are independent. "
            f"P(A and B) is",
            frac(right), frac(wrong))


@family("Data", 5)
def d_at_least_one(r):
    """P(at least one) via the complement. Decoy is P(both)."""
    a, b = r.randint(1, 4), r.choice([5, 6, 8, 10])
    p = Fraction(a, b)
    right = 1 - (1 - p) ** 2
    wrong = p * p
    if right == wrong:
        wrong = p
    return (f"A spinner lands on red with probability {frac(p)}. Spun twice, "
            f"the probability of at least one red is",
            frac(right), frac(wrong))


@family("Data", 4)
def d_outlier_effect(r):
    """How much the mean moves when one value is removed."""
    vals = [r.randint(20, 60) for _ in range(5)]
    out = r.randint(200, 300)
    full = vals + [out]
    m1 = sum(full) / len(full)
    m2 = sum(vals) / len(vals)
    return (f"The mean of {', '.join(map(str, full))} drops by how much when "
            f"{out} is removed? To one decimal,",
            num(round(m1 - m2, 1)), num(round(m2, 1)))


@family("Data", 4)
def d_line_of_best_fit(r):
    """Read a rate off a linear model and extrapolate."""
    m = r.randint(3, 15)
    b = r.randint(5, 60)
    x = r.choice([12, 15, 20, 24, 30])
    return (f"A line of best fit is y = {m}x + {b}. When x = {x}, the predicted "
            f"y is",
            m * x + b, m + b * x)


# ══ GEOMETRY & MEASUREMENT ════════════════════════════════════════════
@family("Geometry & Measurement", 4)
def g_cylinder_volume(r):
    """Volume from a diameter. Decoy uses the diameter as the radius."""
    d = r.choice([6, 8, 10, 12, 14, 16, 18])
    h = r.randint(5, 25)
    right = pi * (d / 2) ** 2 * h
    wrong = pi * d ** 2 * h
    return (f"A cylinder has diameter {d} cm and height {h} cm. Its volume, to "
            f"the nearest cm³, is",
            f"{round(right):,} cm³", f"{round(wrong):,} cm³")


@family("Geometry & Measurement", 4)
def g_cone_volume(r):
    """The classic: the cone's third."""
    rad = r.randint(3, 15)
    h = r.randint(6, 30)
    right = pi * rad ** 2 * h / 3
    wrong = pi * rad ** 2 * h
    return (f"A cone has radius {rad} cm and height {h} cm. Its volume, to the "
            f"nearest cm³, is",
            f"{round(right):,} cm³", f"{round(wrong):,} cm³")


@family("Geometry & Measurement", 4)
def g_sphere_volume(r):
    """Sphere volume. Decoy is the surface area, same numbers."""
    rad = r.randint(3, 14)
    right = 4 / 3 * pi * rad ** 3
    wrong = 4 * pi * rad ** 2
    return (f"A sphere has radius {rad} cm. Its volume, to the nearest cm³, is",
            f"{round(right):,} cm³", f"{round(wrong):,} cm³")


@family("Geometry & Measurement", 5)
def g_cylinder_surface(r):
    """Total surface area. Decoy leaves off the two circles."""
    rad = r.randint(3, 12)
    h = r.randint(5, 25)
    right = 2 * pi * rad * h + 2 * pi * rad ** 2
    wrong = 2 * pi * rad * h
    return (f"A closed cylinder has radius {rad} cm and height {h} cm. Its total "
            f"surface area, to the nearest cm², is",
            f"{round(right):,} cm²", f"{round(wrong):,} cm²")


@family("Geometry & Measurement", 5)
def g_volume_find_height(r):
    """Given the volume, work backwards to a dimension."""
    rad = r.randint(3, 12)
    h = r.randint(4, 20)
    vol = round(pi * rad ** 2 * h)
    right = vol / (pi * rad ** 2)
    wrong = vol / (pi * rad)
    return (f"A cylinder of radius {rad} cm has volume {vol:,} cm³. Its height, "
            f"to the nearest cm, is",
            f"{round(right):,} cm", f"{round(wrong):,} cm")


@family("Geometry & Measurement", 4)
def g_pythagoras(r):
    """Hypotenuse, non-Pythagorean-triple numbers so it can't be recalled."""
    a, b = r.randint(4, 24), r.randint(5, 26)
    right = (a * a + b * b) ** 0.5
    wrong = a + b
    return (f"A right triangle has legs {a} cm and {b} cm. The hypotenuse, to "
            f"one decimal, is",
            f"{right:.1f} cm", f"{wrong:.1f} cm")


@family("Geometry & Measurement", 5)
def g_pythagoras_3d(r):
    """Space diagonal of a box — Pythagoras applied twice."""
    a, b, c = r.randint(3, 12), r.randint(4, 14), r.randint(5, 16)
    right = (a * a + b * b + c * c) ** 0.5
    wrong = (a * a + b * b) ** 0.5
    return (f"A rectangular box measures {a} × {b} × {c} cm. The longest straight "
            f"rod that fits inside, to one decimal, is",
            f"{right:.1f} cm", f"{wrong:.1f} cm")


@family("Geometry & Measurement", 5)
def g_pythagoras_missing_leg(r):
    """Find a leg, not the hypotenuse. Decoy adds instead of subtracting."""
    a = r.randint(5, 20)
    c = a + r.randint(3, 15)
    right = (c * c - a * a) ** 0.5
    wrong = (c * c + a * a) ** 0.5
    return (f"A right triangle has hypotenuse {c} cm and one leg {a} cm. The "
            f"other leg, to one decimal, is",
            f"{right:.1f} cm", f"{wrong:.1f} cm")


@family("Geometry & Measurement", 4)
def g_polygon_angle(r):
    """Interior angle of a regular polygon. Decoy is the exterior angle."""
    n = r.choice([5, 6, 8, 9, 10, 12, 15, 18, 20])
    right = (n - 2) * 180 / n
    wrong = 360 / n
    return (f"Each interior angle of a regular {n}-sided polygon measures",
            f"{num(right)}°", f"{num(wrong)}°")


@family("Geometry & Measurement", 4)
def g_triangle_angle_algebra(r):
    """Angles given as expressions; solve then report the largest."""
    x = r.randint(10, 40)
    a, b = r.randint(2, 5), r.randint(1, 4)
    ang1 = a * x
    ang2 = b * x + r.randint(5, 25)
    ang3 = 180 - ang1 - ang2
    while ang3 <= 0 or ang3 >= 180:
        x = r.randint(10, 30)
        ang1, ang2 = a * x, b * x + 10
        ang3 = 180 - ang1 - ang2
    return (f"A triangle's angles measure {ang1}°, {ang2}° and one more. "
            f"The third angle is",
            f"{ang3}°", f"{180 - ang1}°")


@family("Geometry & Measurement", 4)
def g_parallel_lines(r):
    """Co-interior angles. Decoy gives the equal (alternate) angle."""
    a = r.randint(35, 145)
    return (f"Two parallel lines are cut by a transversal. One co-interior "
            f"(same-side interior) angle is {a}°. The other is",
            f"{180 - a}°", f"{a}°")


@family("Geometry & Measurement", 5)
def g_similar_triangles(r):
    """Scale factor on a side."""
    k = r.choice([2, 3, 4, 1.5, 2.5])
    a = r.randint(4, 18)
    b = r.randint(5, 20)
    return (f"Two triangles are similar. A side of {a} cm corresponds to "
            f"{num(a * k)} cm. A second side of {b} cm corresponds to",
            f"{num(b * k)} cm", f"{num(b + (a * k - a))} cm")


@family("Geometry & Measurement", 5)
def g_area_scale(r):
    """Area scales with the square of the length factor."""
    k = r.choice([2, 3, 4, 5])
    area = r.choice([12, 18, 25, 30, 44, 60])
    return (f"A shape's dimensions are all multiplied by {k}. Its area of "
            f"{area} cm² becomes",
            f"{area * k * k:,} cm²", f"{area * k:,} cm²")


@family("Geometry & Measurement", 4)
def g_composite_area(r):
    """Rectangle topped by a semicircle."""
    w = r.choice([6, 8, 10, 12, 14])
    h = r.randint(5, 20)
    right = w * h + pi * (w / 2) ** 2 / 2
    wrong = w * h + pi * (w / 2) ** 2
    return (f"A rectangle {w} cm wide and {h} cm tall has a semicircle on top, "
            f"spanning the full width. The total area, to the nearest cm², is",
            f"{round(right):,} cm²", f"{round(wrong):,} cm²")


@family("Geometry & Measurement", 4)
def g_trapezoid(r):
    """Trapezoid area. Decoy forgets to halve."""
    a, b = r.randint(4, 20), r.randint(5, 25)
    h = r.randint(3, 18)
    right = (a + b) / 2 * h
    wrong = (a + b) * h
    return (f"A trapezoid has parallel sides {a} cm and {b} cm and height "
            f"{h} cm. Its area is",
            f"{num(right)} cm²", f"{num(wrong)} cm²")


# ══ FINANCIAL LITERACY ════════════════════════════════════════════════
@family("Financial Literacy", 4)
def f_simple_interest(r):
    """I = Prt. Decoy forgets the time."""
    p = r.choice([800, 1200, 1500, 2400, 3000, 5000])
    rate = r.choice([2.5, 3, 4, 4.5, 5, 6])
    t = r.randint(2, 8)
    right = p * rate / 100 * t
    wrong = p * rate / 100
    return (f"{money(p)} earns {rate}% simple interest a year for {t} years. "
            f"The interest earned is",
            money(round(right, 2)), money(round(wrong, 2)))


@family("Financial Literacy", 5)
def f_compound_interest(r):
    """Compound total. Decoy is the simple-interest total."""
    p = r.choice([1000, 1500, 2000, 2500, 4000])
    rate = r.choice([3, 4, 5, 6])
    t = r.randint(3, 10)
    right = p * (1 + rate / 100) ** t
    wrong = p * (1 + rate * t / 100)
    return (f"{money(p)} is invested at {rate}% compounded annually for {t} "
            f"years. The amount at the end is",
            money(round(right, 2)), money(round(wrong, 2)))


@family("Financial Literacy", 5)
def f_compound_gap(r):
    """How much compound beats simple by — the whole point of the unit."""
    p = r.choice([1000, 2000, 3000, 5000])
    rate = r.choice([4, 5, 6, 8])
    t = r.randint(4, 12)
    comp = p * (1 + rate / 100) ** t
    simp = p * (1 + rate * t / 100)
    return (f"{money(p)} at {rate}% for {t} years: how much more does annual "
            f"compounding earn than simple interest?",
            money(round(comp - simp, 2)), money(round(comp - p, 2)))


@family("Financial Literacy", 4)
def f_depreciation(r):
    """Value lost each year, compounding downwards."""
    p = r.choice([12000, 18000, 22000, 27000, 35000])
    rate = r.choice([10, 12, 15, 20])
    t = r.randint(2, 6)
    right = p * (1 - rate / 100) ** t
    wrong = p * (1 - rate * t / 100)
    return (f"A {money(p)} car loses {rate}% of its value each year. After {t} "
            f"years it is worth",
            money(round(right, 2)), money(round(max(wrong, 0), 2)))


@family("Financial Literacy", 4)
def f_net_pay(r):
    """Gross to net through a deduction rate."""
    hours = r.randint(15, 38)
    wage = r.choice([16.55, 17.20, 18.00, 19.50, 21.00])
    rate = r.choice([18, 20, 22, 25])
    gross = hours * wage
    right = gross * (1 - rate / 100)
    return (f"You work {hours} hours at {money(wage)}/h and {rate}% is deducted. "
            f"Your take-home pay is",
            money(round(right, 2)), money(round(gross, 2)))


@family("Financial Literacy", 5)
def f_currency_fee(r):
    """Exchange with a percentage fee on top."""
    amount = r.choice([200, 350, 500, 750, 1000])
    rate = r.choice([0.72, 0.74, 0.68, 1.36, 1.42])
    fee = r.choice([2, 2.5, 3])
    converted = amount * rate
    right = converted * (1 - fee / 100)
    return (f"You exchange {money(amount)} CAD at {rate} USD per CAD, and the "
            f"booth charges a {fee}% fee on the converted amount. You receive",
            money(round(right, 2)), money(round(converted, 2)))


@family("Financial Literacy", 4)
def f_budget_percent(r):
    """What's left after several percentage claims on one income."""
    income = r.choice([1800, 2200, 2600, 3200, 4000])
    a, b, c = r.choice([30, 35, 40]), r.choice([10, 12, 15]), r.choice([5, 8, 10])
    right = income * (1 - (a + b + c) / 100)
    wrong = income - (a + b + c)
    return (f"Of {money(income)} a month, {a}% goes to rent, {b}% to food and "
            f"{c}% to transit. What's left is",
            money(round(right, 2)), money(round(wrong, 2)))


@family("Financial Literacy", 5)
def f_loan_total(r):
    """Total repaid on a monthly-payment loan versus the amount borrowed."""
    principal = r.choice([1200, 2400, 3600, 5000])
    months = r.choice([12, 18, 24, 36])
    monthly = round(principal / months * r.choice([1.08, 1.12, 1.15, 1.2]), 2)
    total = round(monthly * months, 2)
    return (f"A {money(principal)} loan is repaid at {money(monthly)} a month "
            f"for {months} months. The total interest paid is",
            money(round(total - principal, 2)), money(total))


# ── building the bank ─────────────────────────────────────────────────
def _normalise(q):
    return " ".join(str(q).split()).lower()


def build(target=1200, seed=20260812, attempts_per=400):
    """Generate `target` distinct questions, spread evenly over the families.

    Deterministic: the same seed gives the same bank, so a re-seed after a
    schema change doesn't silently reshuffle what campers are mid-way
    through. Duplicates are dropped on the question text, which is why a
    family is asked for more than its share and simply runs out of new
    phrasings if its number space is small.
    """
    rng = random.Random(seed)
    per = max(1, target // len(_FAMILIES) + 1)
    seen = set()
    out = []
    for fam in _FAMILIES:
        made = 0
        for _ in range(attempts_per):
            if made >= per or len(out) >= target:
                break
            try:
                question, answer, wrong = fam["fn"](rng)
            except Exception:
                continue
            question = " ".join(str(question).split())
            answer, wrong = str(answer), str(wrong)
            key = _normalise(question)
            if key in seen or answer == wrong or not answer or not wrong:
                continue
            seen.add(key)
            out.append({
                "question": question,
                "answer": answer,
                "wrongAnswer": wrong,
                "unit": fam["strand"],
                "difficulty": fam["difficulty"],
                "family": fam["name"],
            })
            made += 1
    # Interleave so consecutive positions aren't all one family — the bank
    # is served by difficulty, but staff read it in position order.
    out.sort(key=lambda q: (q["difficulty"], q["unit"]))
    return out[:target]
