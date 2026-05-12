/* ============================================================
   TRAIL — shared stage definitions
   ----------------------------------------------------------------
   Loaded by both /trail.html (the player) and /admin-trail.html
   (the admin editor) so the two views never drift out of sync.
   Exposed as window globals.
   ============================================================ */

/* Letter values — chosen so the final boss expression evaluates to
   27.125. Don't change these without re-balancing STAGES below. */
window.TRAIL_VARS = {
  A: 12, B: 2,  C: 2,  D: 8,  E: 4,  F: 2,  G: 3,  H: 6,  I: 2,  J: 5,
  K: 6,  L: 2,  M: 10, N: 4,  O: 2,  P: 4,  Q: 2,  R: 10, S: 5,  T: 3,
  U: 10, V: 4,  W: 2,  X: 5,  Y: 4,  Z: 10,
};

/* 53 stages: 26 letter-pairs × 2 obstacles + 1 final boss. */
window.TRAIL_STAGES = [
  { kind:'root',   q:'30 − 12',                 a: 18 },
  { kind:'branch', q:'47 + 25',                 a: 72,  unlocks:'A' },
  { kind:'root',   q:'A + 7',                   a: 19 },
  { kind:'branch', q:'A − 4',                   a: 8,   unlocks:'B' },
  { kind:'root',   q:'B + 18',                  a: 20 },
  { kind:'branch', q:'B + 23',                  a: 25,  unlocks:'C' },
  { kind:'root',   q:'C + 14',                  a: 16 },
  { kind:'branch', q:'40 − C',                  a: 38,  unlocks:'D' },
  { kind:'root',   q:'D × 5',                   a: 40 },
  { kind:'branch', q:'D ÷ 2',                   a: 4,   unlocks:'E' },
  { kind:'root',   q:'E × 6',                   a: 24 },
  { kind:'branch', q:'E × 9',                   a: 36,  unlocks:'F' },
  { kind:'root',   q:'F × 11',                  a: 22 },
  { kind:'branch', q:'F × 18',                  a: 36,  unlocks:'G' },
  { kind:'root',   q:'G × 8',                   a: 24 },
  { kind:'branch', q:'G × 7',                   a: 21,  unlocks:'H' },
  { kind:'root',   q:'H + G',                   a: 9 },
  { kind:'branch', q:'H + A',                   a: 18,  unlocks:'I' },
  { kind:'root',   q:'I + B',                   a: 4 },
  { kind:'branch', q:'I + D',                   a: 10,  unlocks:'J' },
  { kind:'root',   q:'J + C',                   a: 7 },
  { kind:'branch', q:'J − F',                   a: 3,   unlocks:'K' },
  { kind:'root',   q:'K + E',                   a: 10 },
  { kind:'branch', q:'K − G',                   a: 3,   unlocks:'L' },
  { kind:'root',   q:'L × H',                   a: 12 },
  { kind:'branch', q:'L × E',                   a: 8,   unlocks:'M' },
  { kind:'root',   q:'M × F',                   a: 20 },
  { kind:'branch', q:'M × I',                   a: 20,  unlocks:'N' },
  { kind:'root',   q:'N × D',                   a: 32 },
  { kind:'branch', q:'N × G',                   a: 12,  unlocks:'O' },
  { kind:'root',   q:'O × J',                   a: 10 },
  { kind:'branch', q:'O × K',                   a: 12,  unlocks:'P' },
  { kind:'root',   q:'P + L × J',               a: 14 },
  { kind:'branch', q:'P + I × H',               a: 16,  unlocks:'Q' },
  { kind:'root',   q:'Q + B + D',               a: 12 },
  { kind:'branch', q:'Q × I + J',               a: 9,   unlocks:'R' },
  { kind:'root',   q:'R + N − K',               a: 8 },
  { kind:'branch', q:'R − L × F',               a: 6,   unlocks:'S' },
  { kind:'root',   q:'S + B − C',               a: 5 },
  { kind:'branch', q:'S × I − K',               a: 4,   unlocks:'T' },
  { kind:'root',   q:'T + ___ = 12',            a: 9 },
  { kind:'branch', q:'___ − T = 7',             a: 10,  unlocks:'U' },
  { kind:'root',   q:'U − ___ = 6',             a: 4 },
  { kind:'branch', q:'___ × 2 = U',             a: 5,   unlocks:'V' },
  { kind:'root',   q:'V + ___ = 10',            a: 6 },
  { kind:'branch', q:'___ × V = 8',             a: 2,   unlocks:'W' },
  { kind:'root',   q:'W + ___ = 9',             a: 7 },
  { kind:'branch', q:'___ − W = J',             a: 7,   unlocks:'X' },
  { kind:'root',   q:'3T + 6Q',                 a: 21 },
  { kind:'branch', q:'(3X + 6Q ÷ G) − B',       a: 17,  unlocks:'Y' },
  { kind:'root',   q:'5L + 3K ÷ J',             a: 13.6 },
  { kind:'branch', q:'(2H + N) ÷ D',            a: 2,   unlocks:'Z' },
  { kind:'final',
    q:'A + M·X / Q − (T·E / Y − G) · L · B − F + (H/K) ÷ (R − W·S/Z + N − J) − Y + U − I·C^O − V = ___ + D/P',
    a: 27.125 },
];

/* Default waypoint path: straight vertical line, last dot near the
   centre of the stage where the log gets dropped. Coordinates are
   fractions of the stage section (x: 0–1, y: 0–1). */
window.TRAIL_DEFAULT_WAYPOINTS = [
  { x: 0.50, y: 0.08 },
  { x: 0.50, y: 0.28 },
  { x: 0.50, y: 0.46 },
  { x: 0.50, y: 0.60 },
  { x: 0.50, y: 0.70 },   // last dot = log position
];
