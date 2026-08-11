"""
THE CLICKER — an idle economy, modelled on Cookie Clicker.
----------------------------------------------------------
A camper owns some number of *clickers*. Each one produces points on its
own, continuously, whether or not the tab is open. Points buy more
clickers, and more clickers buy points faster; efficiency upgrades make
every clicker you own produce more. That's the whole loop.

Two decisions shape everything else here.

**Production is a rate, not a cap.** The old auto-clicker paid a point
every six minutes and then stopped dead once you'd taken 14 points that
day — so the interesting part of owning more clickers (more points) was
capped away, and the last two-thirds of every day produced nothing. There
is no cap now. Instead a single clicker is *slow*: BASE_POINTS_PER_DAY is
14.4, which is exactly what one level used to be allowed to earn in a day.
Left alone for 24 hours, one clicker earns what one level used to earn.
Own ten and you earn ten times that — which is the point of owning ten.

**Production is continuous, so it has to be lossless.** Rate × elapsed is
almost never a whole number of points, and rounding each poll would either
leak points to the camper or quietly steal them. The remainder is banked
as a fraction and carried to the next poll, so a camper who polls every
thirty seconds and one who polls once a day earn exactly the same amount
over the same wall-clock time.

Everything in here is pure — no Flask, no database — so the economy can be
reasoned about and tested on its own. app.py owns persistence.
"""

# ── Buying clickers ───────────────────────────────────────────────────
CLICKER_BASE_COST   = 700     # points for your first clicker
CLICKER_COST_GROWTH = 1.15    # ...and 15% more for each one after, which is
                              # Cookie Clicker's own factor. A flat price
                              # makes the tenth clicker as cheap as the
                              # first, and then the only strategy is to buy
                              # clickers until the game is over.

# ── Efficiency ────────────────────────────────────────────────────────
# One purchase, applied to every clicker you own — so it's worth more the
# more clickers you have, which is what makes the two purchases interesting
# next to each other rather than one being strictly better.
EFFICIENCY_BASE_COST   = 900
EFFICIENCY_COST_GROWTH = 1.55
EFFICIENCY_STEP        = 0.25   # +25% output per level, additive
EFFICIENCY_MAX         = 20     # ×6 output at the top

# ── Output ────────────────────────────────────────────────────────────
# One clicker, no upgrades, no luck: 14.4 points a day. That is deliberately
# the old per-level daily cap, so nobody's income drops the day this ships.
BASE_POINTS_PER_DAY = 14.4
MS_PER_DAY = 24 * 60 * 60 * 1000

# Clickers scale sub-linearly: output is BASE × n^SCALE_EXP, not BASE × n.
#
# The reason is a real camper who already holds a hundred clickers, from
# back when levels were handed out by staff. Linear scaling put that one
# camper at 1,440 points a day, and with luck on top they'd have banked
# 10,000 points in under three days — while a camper with one clicker was
# still on 14.4. The brief was that 10,000 should take at least four days
# from where everyone actually is.
#
# The alternative was cutting BASE, but that punishes the camper with one
# clicker to rein in the camper with a hundred: at the rate needed to fix
# the top, a first clicker would take four months to pay for itself and
# nobody would ever buy one. An exponent leaves the bottom of the curve
# alone and bends the top:
#
#     clickers      1      7     21     100
#     linear     14.4  100.8  302.4  1440.0
#     ^0.85      14.4   75.3  191.5   721.7
#
# which puts the fastest possible run to 10,000 points at 5.8 days with
# maxed luck and reinvestment, and 9+ days idle.
CLICKER_SCALE_EXP = 0.85

# Luck's cut of the clicker economy: up to +50% output at luck 40. The
# discount on buying clickers and the double/triple roll on manual clicks
# live elsewhere (dungeon.discounted and dungeon.luck_point_multiplier) —
# this is the third of luck's three effects here, and the only one that
# touches production.
LUCK_OUTPUT_MAX = 0.50

# A camper who has been away for a month should come back to a month of
# production. A camper whose clock — or ours — has gone wrong should not
# come back to a decade of it. Fourteen days is well past any real absence
# at a summer camp and bounds what a bad timestamp can do.
MAX_OFFLINE_MS = 14 * MS_PER_DAY


# ── Spontaneous duplication ───────────────────────────────────────────
# A clicker can split in two off the back of a single click. It used to be
# a guarantee on every 3,000th click; it's a roll on every click now, at
# 1-in-DUPLICATE_ODDS. The expected rate is unchanged — one duplication per
# 3,000 clicks on average — but it can land on click 12 or not until click
# 9,000, which is the difference between a progress bar and a moment.
DUPLICATE_ODDS = 3000
# Luck leans on it like it leans on everything else here: up to twice the
# chance at luck 40.
LUCK_DUPLICATE_MAX = 1.0
DUPLICATE_LIMIT = 10        # how many a camper can win this way, ever


def duplicate_chance(luck_effectiveness=0.0):
    """Probability that any one click splits a clicker in two."""
    eff = max(0.0, min(float(luck_effectiveness or 0.0), 1.0))
    return (1.0 / DUPLICATE_ODDS) * (1.0 + LUCK_DUPLICATE_MAX * eff)


def rolls_duplicate(luck_effectiveness=0.0, roll=None):
    """Did this click get lucky? `roll` is injectable for tests."""
    import secrets
    r = float(roll) if roll is not None else secrets.randbelow(10**9) / 10**9
    return r < duplicate_chance(luck_effectiveness)


def clicker_cost(owned):
    """Points for the NEXT clicker when you already own `owned`."""
    n = max(0, int(owned))
    return int(round(CLICKER_BASE_COST * (CLICKER_COST_GROWTH ** n)))


def clickers_cost(owned, qty):
    """Total for `qty` more clickers, priced one at a time as the count
    climbs — buying five in one click costs exactly what buying five
    separately would."""
    owned = max(0, int(owned))
    total = 0
    for i in range(max(0, int(qty))):
        total += clicker_cost(owned + i)
    return total


def efficiency_cost(level):
    """Points for the NEXT efficiency level when you're at `level`.
    None once there's nothing left to buy."""
    lv = max(0, int(level))
    if lv >= EFFICIENCY_MAX:
        return None
    return int(round(EFFICIENCY_BASE_COST * (EFFICIENCY_COST_GROWTH ** lv)))


def efficiency_multiplier(level):
    """What efficiency does to output. Additive per level, so level 4 is
    ×2 rather than the runaway a compounding step would give."""
    lv = max(0, min(int(level), EFFICIENCY_MAX))
    return 1.0 + EFFICIENCY_STEP * lv


def luck_multiplier(luck_effectiveness):
    """Luck's share of output, from the same 0–1 effectiveness curve every
    other luck effect uses."""
    eff = max(0.0, min(float(luck_effectiveness or 0.0), 1.0))
    return 1.0 + LUCK_OUTPUT_MAX * eff


def points_per_day(clickers, efficiency=0, luck_effectiveness=0.0):
    """The headline number: points a day at this loadout.

    Sub-linear in the clicker count — see CLICKER_SCALE_EXP for why. The
    first clicker is worth a full BASE_POINTS_PER_DAY; the hundredth is
    worth a fraction of one.
    """
    n = max(0, int(clickers))
    if n <= 0:
        return 0.0
    return (BASE_POINTS_PER_DAY * (n ** CLICKER_SCALE_EXP)
            * efficiency_multiplier(efficiency)
            * luck_multiplier(luck_effectiveness))


def points_per_ms(clickers, efficiency=0, luck_effectiveness=0.0):
    return points_per_day(clickers, efficiency, luck_effectiveness) / MS_PER_DAY


def accrue(clickers, efficiency, luck_effectiveness, elapsed_ms, bank=0.0):
    """Work out what `elapsed_ms` of production is worth.

    Returns (whole_points, new_bank). `bank` is the fraction of a point
    carried over from last time; the fraction left this time is returned
    to be carried again. Nothing is ever rounded away — poll frequency
    cannot change what a camper earns over a given stretch of time, which
    is the property that stops "click refresh faster" from being a
    strategy.
    """
    elapsed = max(0, int(elapsed_ms or 0))
    elapsed = min(elapsed, MAX_OFFLINE_MS)
    carried = float(bank or 0.0)
    if carried < 0:
        carried = 0.0
    if int(clickers or 0) <= 0 or elapsed <= 0:
        return 0, carried
    total = carried + points_per_ms(clickers, efficiency, luck_effectiveness) * elapsed
    whole = int(total)              # total is never negative, so this floors
    return whole, total - whole


def seconds_per_point(clickers, efficiency=0, luck_effectiveness=0.0):
    """How long one point takes at this loadout — None if nothing is
    producing. Display only."""
    per_day = points_per_day(clickers, efficiency, luck_effectiveness)
    if per_day <= 0:
        return None
    return (24 * 60 * 60) / per_day


def summary(clickers, efficiency=0, luck_effectiveness=0.0, points=0):
    """Everything the clicker panel needs to draw itself."""
    n = max(0, int(clickers))
    lv = max(0, min(int(efficiency), EFFICIENCY_MAX))
    per_day = points_per_day(n, lv, luck_effectiveness)
    eff_cost = efficiency_cost(lv)
    return {
        "clickers": n,
        "efficiency": lv,
        "efficiencyMax": EFFICIENCY_MAX,
        "efficiencyMultiplier": round(efficiency_multiplier(lv), 4),
        "luckMultiplier": round(luck_multiplier(luck_effectiveness), 4),
        "perDay": round(per_day, 2),
        "perHour": round(per_day / 24, 3),
        "secondsPerPoint": (round(seconds_per_point(n, lv, luck_effectiveness))
                            if per_day > 0 else None),
        "nextClickerCost": clicker_cost(n),
        "nextEfficiencyCost": eff_cost,
        "basePerDay": BASE_POINTS_PER_DAY,
        # What one more of each would add, so the panel can say why you'd
        # buy one over the other rather than making the camper work it out.
        "gainPerClicker": round(points_per_day(n + 1, lv, luck_effectiveness) - per_day, 2),
        "gainPerEfficiency": (round(points_per_day(n, lv + 1, luck_effectiveness) - per_day, 2)
                              if eff_cost is not None else 0),
        "affordableClickers": _affordable(n, points),
    }


def _affordable(owned, points):
    """How many more clickers `points` would buy at the escalating price."""
    budget = max(0, int(points or 0))
    n, spent = 0, 0
    while n < 500:                     # sanity bound; nobody buys 500 at once
        nxt = clicker_cost(owned + n)
        if spent + nxt > budget:
            break
        spent += nxt
        n += 1
    return n
