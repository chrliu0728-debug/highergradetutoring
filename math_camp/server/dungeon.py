"""Dungeon economy — the item catalogue and every number that derives from it.

Deliberately free of Flask and the database so the maths can be read, reasoned
about and tested on its own. app.py owns persistence and the HTTP surface;
everything here is a pure function over plain dicts.

The rules encoded here were settled question-by-question with the camp
organiser; where a choice isn't obvious from the formula, the comment says
which way it was decided and why.
"""

import math

# ── Currency ──────────────────────────────────────────────────────────
SHARD_ICON            = "💎"
SHARDS_PER_POINT      = 100     # cashing shards IN:  100 shards -> 1 point
SHARDS_FROM_POINT     = 80      # cashing points OUT: 1 point   -> 80 shards
# Every trip to the counter costs this, in points, on top of the rates and
# the tax. Rates alone never stopped the round trip being worth doing at
# volume — a flat fee does, because it doesn't scale with the amount.
CONVERSION_FEE_POINTS = 150
# The spread is intentional: a round trip loses 20%, so converting is a
# decision rather than a free shuffle.

TAX_RATE              = 0.13    # withheld on every earn AND every spend

# ── Floors ────────────────────────────────────────────────────────────
FLOOR_BASE            = 100
FLOOR_TIER_STEP       = 10      # tier bumps every 10 floors...
FLOOR_TIER_GROWTH     = 1.30    # ...and each tier pays 30% more, compounding
# ...but only so far. Compounding forever is fine while runs end early, and
# a runaway once they don't: a near-perfect camper who threads 300 floors
# would be earning six figures per door. The cap sits at floor 121, past
# the p90 depth of a 97% player, so it never touches ordinary play — it
# only stops the rare flawless run from printing money.
FLOOR_TIER_CAP        = 12
FLOOR_TIME_LIMIT_MS   = 3 * 60 * 1000    # 3 min, then you're moved on for free
# Three minutes is long enough to actually work a Grade 9 problem out on
# paper and far too short to sit on a floor waiting for inspiration.

# ── Speed ─────────────────────────────────────────────────────────────
BASE_WINDOW_S         = 2.0     # full ×2.0 inside this many seconds
SPEED_MAX             = 2.0
SPEED_MIN             = 0.5
SPEED_FLOOR_S         = 150.0   # where the curve bottoms out at ×0.5

# ── Health ────────────────────────────────────────────────────────────
BASE_MAX_HP           = 1000
DAMAGE_FAST_WRONG     = 120     # wrong door inside the window, on floor 1
DAMAGE_WRONG          = 60      # wrong door after it, and every later one
FAST_WRONG_CUTOFF_S   = 2.0
# Deeper floors hit harder. Without this a strong camper never dies: at the
# ~97% accuracy these players actually have, a flat 120 means one mistake
# every ~33 floors and death somewhere past floor 280 — by which point a
# single floor pays six figures, because the reward curve compounds 30%
# every ten floors and nothing was stopping it. Scaling damage ends runs
# around floor 60–90 and, as a side effect, keeps rewards bounded: nobody
# survives long enough to reach the runaway tiers.
DAMAGE_FLOOR_STEP_FAST = 12     # extra damage per floor on a fast wrong door
DAMAGE_FLOOR_STEP_SLOW = 6      # ...and on every later one
# Two doors means a coin flip clears half of them, and a flat penalty for
# a wrong door made guessing a viable strategy: 1000 HP at 60 a miss is
# sixteen free guesses on floor 1. Each REPEAT mistake on the same floor
# now hits harder than the last, so standing at one door guessing burns
# you out in four or five tries. Someone who knows the answer and slips
# once pays the old price — the escalation only bites the second mistake
# on the same floor onward.
WRONG_REPEAT_GROWTH   = 1.6
DEFENSE_SOFTCAP       = 600     # defense/(defense+600) — approaches, never hits 100%
WRONG_DOOR_SHARD_LOSS = 0.90    # of what that door would actually have paid

# ── Death / banking ───────────────────────────────────────────────────
DEATH_KEEP_FRACTION   = 0.70    # die -> keep 70% of the run; walk out -> 100%

# ── Luck ──────────────────────────────────────────────────────────────
LUCK_MAX              = 40
LUCK_BASE_COST        = 200     # points, matching the pre-existing luck buy
LUCK_COST_GROWTH      = 1.12    # 200 × 1.12^(L-1); ~153k points to reach 40
LUCK_AMPLIFY_PER_LEVEL = 0.05   # +5% to other effects per level
LUCK_WINDOW_MS_PER_LEVEL = 1    # +1ms to the doubling window per level
LUCK_DISCOVERY_CHANCE = 0.02    # 2% per floor
LUCK_DISCOVERY_BASE   = 1200
# Luck outside the dungeon. Every one of these scales with the same
# effectiveness curve, so they arrive gradually rather than switching on:
# at luck 10 (65% effective) a camper sees roughly a third of their awards
# multiplied and a fifth off the till, and at 40 the ceilings below are the
# real numbers. The ceilings are deliberately short of certainty — staff
# still need a deduction to land and a shop still needs to charge.
LUCK_DOUBLE_CHANCE_MAX   = 0.50   # of an award being doubled, at luck 40
LUCK_TRIPLE_CHANCE_MAX   = 0.10   # ...or tripled instead, at luck 40
LUCK_DEDUCTION_SHRUG_MAX = 0.50   # of a deduction bouncing off, at luck 40
LUCK_DISCOUNT_MAX        = 0.30   # off anything you pay for, at luck 40

# ── Shop ──────────────────────────────────────────────────────────────
INTERMEDIATE_UNLOCK_FLOOR = 50
COUNTERPART_DISCOUNT  = 0.20    # own the beginner item -> 20% off its upgrade
ADMIN_SHARD_GRANT     = 300_000

SLOTS = ("helmet", "chestplate", "leggings", "boots",
         "weapon", "back", "necklace", "amulet", "ring", "earring")


def _item(**kw):
    """One catalogue row, with every optional stat defaulted so callers can
    read any field off any item without guarding."""
    base = {
        "id": "", "name": "", "tier": "beginner", "slot": None,
        "cost": 0, "blurb": "",
        "shardBonus": 0.0,      # additive with other shard bonuses
        "maxHp": 0,
        "defense": 0,
        "window": None,         # seconds; largest equipped value wins, no stacking
        "evadeChance": 0.0,     # dodge the hit entirely
        "negateChance": 0.0,    # same, rolled separately
        "flatNegate": 0.0,      # % of damage removed AFTER the defense formula
        "stackable": False,
        "capacity": 0,          # quivers
        "consumable": False,
        "reveals": None,        # 'before-animation' | 'on-door-click'
        "counterpart": None,    # beginner item whose ownership discounts this
        "realWorld": False,
        "quest": False,         # handed out by the story; never on a shelf
        "note": None,           # readable text, re-openable from the inventory
    }
    base.update(kw)
    return base


# ── The catalogue ─────────────────────────────────────────────────────
# Leather keeps its (weak) stats but its price was cut to ~18k for the set;
# it's a cheap stopgap on the way to iron rather than a serious tier.
ITEMS = {i["id"]: i for i in [
    # ── Beginner ──
    _item(id="crappy_dagger", name="Crappy Dagger", slot="weapon", cost=15000,
          shardBonus=0.05, window=2.5,
          blurb="Chipped, but it pays. +5% shards and a slightly longer window."),
    _item(id="common_shield", name="Common Shield", slot="back", cost=10000,
          evadeChance=0.10,
          blurb="Dented tin. 10% chance to avoid a hit outright."),
    _item(id="leather_boots", name="Leather Boots", slot="boots", cost=3000,
          maxHp=2, defense=1, blurb="Barely boots. +2 HP, +1 defense."),
    _item(id="leather_leggings", name="Leather Leggings", slot="leggings", cost=5500,
          maxHp=4, defense=3, blurb="Scuffed hide. +4 HP, +3 defense."),
    _item(id="leather_chestplate", name="Leather Chestplate", slot="chestplate", cost=6000,
          maxHp=5, defense=3, blurb="Better than nothing. +5 HP, +3 defense."),
    _item(id="leather_helmet", name="Leather Helmet", slot="helmet", cost=3500,
          maxHp=3, blurb="A cap, really. +3 HP."),
    _item(id="common_bow", name="Common Bow", slot="weapon", cost=12000,
          shardBonus=0.10, window=3.0,
          blurb="Draw before the door. +10% shards and a 3s window, per shot."),
    _item(id="basic_arrow", name="Basic Arrow", cost=150, stackable=True, consumable=True,
          blurb="Ammunition. One per bow shot."),
    _item(id="leather_quiver", name="Leather Quiver", slot="back", cost=5000, capacity=50,
          blurb="Holds up to 50 arrows."),
    _item(id="cheap_long_sword", name="Cheap Long Sword", slot="weapon", cost=10000,
          window=3.5, blurb="No shard bonus, but a generous 3.5s window."),
    _item(id="crappy_hammer", name="Crappy Hammer", slot="weapon", cost=12000,
          shardBonus=0.10,
          blurb="Heavy and slow — no window help, but +10% shards."),

    # ── Intermediate (door 50) ──
    _item(id="basic_amulet", name="Basic Amulet", tier="intermediate", slot="amulet",
          cost=8000, shardBonus=0.02, blurb="A modest charm. +2% shards."),
    _item(id="starting_necklace", name="Starting Necklace", tier="intermediate",
          slot="necklace", cost=20000, reveals="before-animation",
          blurb="Shows the next question before the doors finish forming."),
    _item(id="signet_ring", name="Signet Ring", tier="intermediate", slot="ring",
          cost=12000, shardBonus=0.03, defense=5,
          blurb="Barely anything, honestly. +3% shards, +5 defense."),
    _item(id="earring", name="Earring", tier="intermediate", slot="earring", cost=10000,
          consumable=True,
          blurb="Negates one killing blow, then shatters."),
    _item(id="better_dagger", name="Better Dagger", tier="intermediate", slot="weapon",
          cost=20000, shardBonus=0.08, window=3.5, counterpart="crappy_dagger",
          blurb="Actually sharp. +8% shards, 3.5s window."),
    _item(id="longer_sword", name="Longer Sword", tier="intermediate", slot="weapon",
          cost=20000, negateChance=0.05, window=4.5, counterpart="cheap_long_sword",
          blurb="Reach. 5% chance to negate a hit, 4.5s window."),
    _item(id="hammer", name="Hammer", tier="intermediate", slot="weapon", cost=18000,
          shardBonus=0.15, window=4.0, counterpart="crappy_hammer",
          blurb="Big money, big swing. +15% shards, 4s window."),
    _item(id="elven_bow", name="Elven Bow", tier="intermediate", slot="weapon", cost=22000,
          shardBonus=0.10, window=4.0, reveals="on-door-click", counterpart="common_bow",
          blurb="Reveals the next question as you take the door. 4s window, +10% shards."),
    _item(id="drill_needle_arrow", name="Drill Needle Arrow", tier="intermediate",
          cost=400, stackable=True, consumable=True,
          blurb="3% chance to either soften a hit by 20% or pay 10% more shards."),
    _item(id="well_crafted_quiver", name="Well Crafted Quiver", tier="intermediate",
          slot="back", cost=18000, capacity=80, counterpart="leather_quiver",
          blurb="Holds 80 arrows, and 1% of them come out piercing."),
    _item(id="well_crafted_shield", name="Well Crafted Shield", tier="intermediate",
          slot="back", cost=21000, flatNegate=0.22, counterpart="common_shield",
          blurb="Negates 22% of all incoming damage."),
    _item(id="iron_boots", name="Iron Boots", tier="intermediate", slot="boots",
          cost=31000, maxHp=40, defense=20, counterpart="leather_boots",
          blurb="+40 HP, +20 defense."),
    _item(id="iron_helmet", name="Iron Helmet", tier="intermediate", slot="helmet",
          cost=28000, maxHp=60, defense=10, counterpart="leather_helmet",
          blurb="+60 HP, +10 defense."),
    _item(id="iron_leggings", name="Iron Leggings", tier="intermediate", slot="leggings",
          cost=34000, maxHp=80, defense=50, counterpart="leather_leggings",
          blurb="+80 HP, +50 defense."),
    _item(id="iron_chestplate", name="Iron Chestplate", tier="intermediate",
          slot="chestplate", cost=38000, maxHp=100, defense=70,
          counterpart="leather_chestplate",
          blurb="+100 HP, +70 defense."),

    # ── Real-world ──
    _item(id="phone_privileges", name="Phone Privileges", tier="reward", cost=100000,
          realWorld=True,
          blurb="One class period with your phone. Staff will confirm it with you."),

    # ── Quest items ──
    # Earned, never bought. `quest` is what keeps them off the shop shelf and
    # out of the buy endpoint — they carry no cost, so without it they'd be
    # free for the taking. They sit in the bag as trophies and reminders
    # rather than gear, which is why neither takes an equipment slot.
    _item(id="lime_sword", name="Lime Sword", tier="quest", quest=True,
          blurb="Deals 3000 damage per swing to Arachnids."),
    _item(id="spider_hunt_note", name="A note, in the blade's hand", tier="quest",
          quest=True,
          blurb="Fell out of the Lime Sword the moment it reforged.",
          note="Time to go on a spider **hunt**."),
]}

# Whatever the story hands over for reforging the Lime Sword. Kept here so
# the claim endpoint doesn't hard-code ids the catalogue already owns.
LIME_SWORD_REWARD = ("lime_sword", "spider_hunt_note")

# A cleared trial pays shards on the run's score. 200 limes cut perfectly is
# 200k points, so a flawless run is worth ~333 shards — real money at the
# bottom of the shop, nowhere near a shortcut past it.
LIME_SCORE_PER_SHARD = 600

# Items a brand-new camper may pick ONE of, free, on first entry. Everything
# else is bought — points convert to shards, and gear is a convenience rather
# than a requirement.
STARTER_CHOICES = ("crappy_dagger", "cheap_long_sword", "crappy_hammer",
                   "common_shield", "leather_helmet")

ARROWS = ("basic_arrow", "drill_needle_arrow")


# ── Floors ────────────────────────────────────────────────────────────
def base_shards(floor):
    """Base payout for clearing `floor`. Tier bumps land on 11, 21, 31…
    and stop climbing at FLOOR_TIER_CAP."""
    tier = max(0, (int(floor) - 1) // FLOOR_TIER_STEP)
    return round(FLOOR_BASE * (FLOOR_TIER_GROWTH ** min(tier, FLOOR_TIER_CAP)))


# ── Speed ─────────────────────────────────────────────────────────────
def speed_multiplier(seconds, window=BASE_WINDOW_S):
    """×2.0 flat inside the window, decaying to ×0.5 at 150s however wide
    the window is. Rewritten from the spec's fixed-constant curve so an
    extended window stretches the curve rather than shifting it off the
    ×0.5-at-150s anchor."""
    t = max(0.0, float(seconds))
    w = max(0.0, float(window))
    if t <= w:
        return SPEED_MAX
    span = SPEED_FLOOR_S - w
    if span <= 0:
        return SPEED_MIN
    # 2.0 × 0.25^((t-w)/span): hits 0.5 exactly at t = 150.
    mult = SPEED_MAX * (0.25 ** ((t - w) / span))
    return max(SPEED_MIN, min(SPEED_MAX, mult))


# ── Luck ──────────────────────────────────────────────────────────────
def luck_effectiveness(luck):
    """ln(1+L)/ln(41) — 100% at 40, which the organiser confirmed means a
    literal guarantee: doubled shards and fully negated damage."""
    lv = max(0, min(int(luck), LUCK_MAX))
    if lv <= 0:
        return 0.0
    return min(1.0, math.log(1 + lv) / math.log(LUCK_MAX + 1))


def luck_level_cost(next_level):
    """Points for the level that takes you TO `next_level` (1-indexed)."""
    n = max(1, int(next_level))
    return round(LUCK_BASE_COST * (LUCK_COST_GROWTH ** (n - 1)))


def luck_total_cost(levels=LUCK_MAX):
    return sum(luck_level_cost(n) for n in range(1, int(levels) + 1))


def luck_point_multiplier(luck, roll=None):
    """Does this award land doubled, tripled, or as-is?

    Returns (multiplier, label). One roll decides all three outcomes, so a
    triple is a better version of a double rather than a second lottery on
    top of it — which keeps the headline odds honest: at luck 40 half of
    all awards are multiplied, and a fifth of those are triples.

    `roll` is injectable so tests can pin it.
    """
    eff = luck_effectiveness(luck)
    if eff <= 0:
        return 1, None
    r = _roll(roll)
    if r < eff * LUCK_TRIPLE_CHANCE_MAX:
        return 3, "triple"
    if r < eff * LUCK_DOUBLE_CHANCE_MAX:
        return 2, "double"
    return 1, None


def luck_shrugs_deduction(luck, roll=None):
    """True if a deduction bounces off entirely. Caps at 50% so that a
    maxed-out camper is lucky, not untouchable — staff need the other half
    of their deductions to actually land."""
    eff = luck_effectiveness(luck)
    return eff > 0 and _roll(roll) < eff * LUCK_DEDUCTION_SHRUG_MAX


def luck_discount(luck):
    """Fraction off anything the camper pays for. Unlike the two rolls
    above this one is certain — it's a standing discount, not a gamble, so
    a price can be quoted honestly before the camper commits."""
    return luck_effectiveness(luck) * LUCK_DISCOUNT_MAX


def discounted(price, luck):
    """(payable, saved) for `price` at this luck. Rounds in the camper's
    favour and never goes below zero."""
    p = max(0, int(price))
    payable = max(0, round(p * (1.0 - luck_discount(luck))))
    return payable, p - payable


def _roll(roll=None):
    if roll is not None:
        return float(roll)
    import random as _r
    return _r.random()


def luck_discovery(luck_points_spent, floor):
    """2%-per-floor find. Precedence resolved as 1200 + (spent / floor), paid
    in shards — so the luck term is a nice early kicker that thins out with
    depth rather than shrinking the whole reward to nothing."""
    fl = max(1, int(floor))
    return round(LUCK_DISCOVERY_BASE + (max(0, int(luck_points_spent)) / fl))


# ── Loadout ───────────────────────────────────────────────────────────
def loadout(equipped, luck=0):
    """Fold every equipped item into one set of numbers.

    `equipped` is {slot: itemId}. Percentage shard bonuses are additive with
    each other and applied as a single multiplier, so a +5% dagger and a +10%
    hammer give ×1.15 rather than ×1.155. Window extensions do NOT stack —
    the widest equipped window wins.
    """
    lv = max(0, min(int(luck or 0), LUCK_MAX))
    amp = 1.0 + LUCK_AMPLIFY_PER_LEVEL * lv       # luck amplifies other effects

    out = {
        "shardBonus": 0.0, "maxHp": BASE_MAX_HP, "defense": 0,
        "window": BASE_WINDOW_S, "evadeChance": 0.0, "negateChance": 0.0,
        "flatNegate": 0.0, "capacity": 0, "reveals": None,
        "hasEarring": False, "items": [],
    }
    for slot, item_id in (equipped or {}).items():
        it = ITEMS.get(item_id)
        if not it or slot not in SLOTS:
            continue
        out["items"].append(item_id)
        out["shardBonus"] += it["shardBonus"]
        out["maxHp"]      += it["maxHp"]
        out["defense"]    += it["defense"]
        out["evadeChance"] = max(out["evadeChance"], it["evadeChance"])
        out["negateChance"] = max(out["negateChance"], it["negateChance"])
        out["flatNegate"] = max(out["flatNegate"], it["flatNegate"])
        out["capacity"]  += it["capacity"]
        if it["window"]:
            out["window"] = max(out["window"], it["window"])
        if it["reveals"]:
            out["reveals"] = it["reveals"]
        if slot == "earring":
            out["hasEarring"] = True

    # Luck: +1 to every base stat per level, +1ms of window per level, and a
    # 5%-per-level amplification of the percentage effects.
    out["shardBonus"] *= amp
    out["maxHp"]    += lv
    out["defense"]  += lv
    out["window"]   += lv * (LUCK_WINDOW_MS_PER_LEVEL / 1000.0)
    out["evadeChance"]  = min(0.95, out["evadeChance"] * amp)
    out["negateChance"] = min(0.95, out["negateChance"] * amp)
    out["flatNegate"]   = min(0.95, out["flatNegate"] * amp)
    return out


# ── Rewards ───────────────────────────────────────────────────────────
def floor_reward(floor, seconds, gear, luck=0, arrow_bonus=1.0):
    """Shards for clearing a floor. Rounds once, at the end."""
    base  = base_shards(floor)
    speed = speed_multiplier(seconds, gear["window"])
    eff   = luck_effectiveness(luck)
    # Luck's headline effect: at 40 the double is guaranteed, below that it's
    # a proportional chance expressed as an expected multiplier so the payout
    # stays smooth rather than swinging on a coin flip.
    luck_mult = 1.0 + eff
    return max(0, round(base * speed * (1.0 + gear["shardBonus"])
                        * luck_mult * float(arrow_bonus)))


def wrong_door_loss(floor, seconds, gear, luck=0):
    """90% of what that door would actually have paid at this answer speed —
    so a fast wrong click is a big gamble in both directions."""
    return max(0, round(WRONG_DOOR_SHARD_LOSS
                        * floor_reward(floor, seconds, gear, luck)))


# ── Damage ────────────────────────────────────────────────────────────
def wrong_door_damage(floor, fast, repeats=0):
    """Raw damage for a wrong door on `floor`, before armour.

    Floor 1 is the spec's 120 / 60. Every floor after adds to it, so the
    dungeon gets genuinely dangerous the deeper you push rather than being
    a formality for anyone who knows the answers.

    `repeats` is how many times this camper has already picked wrong on
    THIS floor. Each one multiplies the hit, which is what makes guessing
    at a two-door question a losing strategy rather than a slow one.
    """
    f = max(1, int(floor)) - 1
    if fast:
        base = DAMAGE_FAST_WRONG + DAMAGE_FLOOR_STEP_FAST * f
    else:
        base = DAMAGE_WRONG + DAMAGE_FLOOR_STEP_SLOW * f
    return round(base * (WRONG_REPEAT_GROWTH ** max(0, int(repeats))))


def damage_reduction(total_defense):
    d = max(0, int(total_defense))
    return d / (d + DEFENSE_SOFTCAP)


def apply_damage(raw, gear, luck=0, rolls=None):
    """Resolve one hit. `rolls` lets tests pin the RNG; in production it's
    None and random is used.

    Order: luck's guaranteed negation, then evade, then the chance-negate,
    then the defense curve, then the shield's flat percentage.
    """
    import random as _r
    roll = (lambda k: rolls[k]) if rolls else (lambda k: _r.random())

    eff = luck_effectiveness(luck)
    # At 40 luck eff == 1.0 and this always fires: damage is fully negated.
    if eff >= 1.0 or roll("luck") < eff:
        return 0, "luck"
    if gear["evadeChance"] > 0 and roll("evade") < gear["evadeChance"]:
        return 0, "evade"
    if gear["negateChance"] > 0 and roll("negate") < gear["negateChance"]:
        return 0, "negate"

    dealt = raw * (1.0 - damage_reduction(gear["defense"]))
    dealt *= (1.0 - gear["flatNegate"])
    return max(0, round(dealt)), None


# ── Tax ───────────────────────────────────────────────────────────────
def split_tax(gross):
    """13% withheld. Tax rounds up so net + tax always equals gross exactly
    and the ledger never drifts by a shard."""
    g = max(0, int(gross))
    tax = math.ceil(g * TAX_RATE)
    return g - tax, tax


def purchase_total(price):
    """What a shelf price actually costs at the till. Tax is charged on top
    and shown as its own line, never folded into the headline number."""
    p = max(0, int(price))
    tax = math.ceil(p * TAX_RATE)
    return p + tax, tax


def price_for(item_id, owned_ids):
    """Shelf price after the 20% counterpart discount, plus what was taken
    off, so the shop can label the saving instead of silently applying it."""
    it = ITEMS.get(item_id)
    if not it:
        return None
    full = it["cost"]
    cp = it.get("counterpart")
    if cp and cp in set(owned_ids or ()):
        discounted = round(full * (1.0 - COUNTERPART_DISCOUNT))
        return {"full": full, "price": discounted, "saved": full - discounted,
                "counterpart": cp}
    return {"full": full, "price": full, "saved": 0, "counterpart": None}


# ── Conversion ────────────────────────────────────────────────────────
def shards_to_points(shards):
    """Whole points only; the remainder stays in shards."""
    s = max(0, int(shards))
    pts = s // SHARDS_PER_POINT
    return pts, pts * SHARDS_PER_POINT


def points_to_shards(points):
    p = max(0, int(points))
    return p * SHARDS_FROM_POINT
