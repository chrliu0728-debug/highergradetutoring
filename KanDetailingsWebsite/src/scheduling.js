// Slot generation + the booking window rules.
//
// The rules the customer must satisfy, enforced here and re-checked server-side
// on submit so a hand-crafted POST cannot slip past the UI:
//
//   * every slot is exactly 3 hours long and starts on a published window
//   * every slot starts at least 36 hours after the moment they register
//   * every slot starts within 16 days of the moment they register
//   * they pick exactly 4 distinct slots, ranked 1st through 4th choice

import { db } from './db.js';

export const SHOP_TIMEZONE = process.env.SHOP_TIMEZONE || 'America/Vancouver';

export const SLOT_HOURS = 3;
export const REQUIRED_SLOTS = 4;
export const MIN_LEAD_HOURS = 36;
export const MAX_WINDOW_DAYS = 16;

/** Local start hours for each 3-hour window. */
const WINDOW_START_HOURS = [8, 11, 14, 17];

/** 0 = Sunday. The bay is closed Sundays. */
const CLOSED_WEEKDAYS = new Set([0]);

/** How many cars the crew can take in one window. */
export const SLOT_CAPACITY = Number(process.env.SLOT_CAPACITY || 2);

const MS_HOUR = 3600_000;
const MS_DAY = 24 * MS_HOUR;

function tzOffsetMs(date, timeZone) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone,
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
      .formatToParts(date)
      .map((p) => [p.type, p.value])
  );
  const asUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour) % 24,
    Number(parts.minute),
    Number(parts.second)
  );
  return asUtc - date.getTime();
}

/** Wall-clock time in SHOP_TIMEZONE -> the UTC instant it refers to. */
function zonedToUtc(year, month, day, hour, timeZone = SHOP_TIMEZONE) {
  const naive = Date.UTC(year, month - 1, day, hour, 0, 0);
  // One refinement pass settles DST transitions.
  let ts = naive - tzOffsetMs(new Date(naive), timeZone);
  ts = naive - tzOffsetMs(new Date(ts), timeZone);
  return new Date(ts);
}

/** The calendar date + weekday in SHOP_TIMEZONE for a given instant. */
function zonedParts(date, timeZone = SHOP_TIMEZONE) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone,
      hour12: false,
      weekday: 'short',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
      .formatToParts(date)
      .map((p) => [p.type, p.value])
  );
  const weekdayIndex = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(parts.weekday);
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    weekday: weekdayIndex,
    weekdayLabel: parts.weekday,
  };
}

export function earliestStart(from = new Date()) {
  return new Date(from.getTime() + MIN_LEAD_HOURS * MS_HOUR);
}

export function latestStart(from = new Date()) {
  return new Date(from.getTime() + MAX_WINDOW_DAYS * MS_DAY);
}

/**
 * Every bookable slot between `earliest` and `latest`, grouped by local day.
 * Slots already at capacity are still returned but flagged `full`, so the
 * customer can see why a window is greyed out instead of it silently missing.
 */
export function availableSlots(from = new Date()) {
  const earliest = earliestStart(from);
  const latest = latestStart(from);
  const taken = confirmedCounts();

  const days = [];
  // Walk local calendar days from the day `earliest` lands on, through `latest`.
  for (let offset = 0; offset <= MAX_WINDOW_DAYS + 1; offset++) {
    const probe = new Date(earliest.getTime() + offset * MS_DAY);
    const { year, month, day, weekday, weekdayLabel } = zonedParts(probe);
    if (CLOSED_WEEKDAYS.has(weekday)) continue;

    const slots = [];
    for (const hour of WINDOW_START_HOURS) {
      const start = zonedToUtc(year, month, day, hour);
      if (start < earliest || start > latest) continue;
      const end = new Date(start.getTime() + SLOT_HOURS * MS_HOUR);
      const key = start.toISOString();
      const used = taken.get(key) || 0;
      slots.push({
        start: key,
        end: end.toISOString(),
        label: formatTimeRange(start, end),
        full: used >= SLOT_CAPACITY,
        remaining: Math.max(0, SLOT_CAPACITY - used),
      });
    }
    if (!slots.length) continue;

    const dayKey = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    if (days.some((d) => d.date === dayKey)) continue;
    days.push({
      date: dayKey,
      weekday: weekdayLabel,
      label: formatDay(slots[0].start),
      slots,
    });
  }
  return days;
}

function confirmedCounts() {
  const rows = db
    .prepare(
      `SELECT confirmed_slot AS slot, COUNT(*) AS n
         FROM bookings
        WHERE confirmed_slot IS NOT NULL AND status IN ('confirmed', 'in_progress')
        GROUP BY confirmed_slot`
    )
    .all();
  return new Map(rows.map((r) => [r.slot, r.n]));
}

export function formatDay(iso) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: SHOP_TIMEZONE,
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  }).format(new Date(iso));
}

export function formatTimeRange(start, end) {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: SHOP_TIMEZONE,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
  return `${fmt.format(new Date(start))} – ${fmt.format(new Date(end))}`;
}

export function formatSlot(iso) {
  const start = new Date(iso);
  const end = new Date(start.getTime() + SLOT_HOURS * MS_HOUR);
  return `${formatDay(start)}, ${formatTimeRange(start, end)}`;
}

/**
 * Validate the four slot starts a customer submitted.
 * Returns { ok: true, slots } or { ok: false, error }.
 */
export function validateSlotSelection(starts, from = new Date()) {
  if (!Array.isArray(starts)) {
    return { ok: false, error: 'Time slots must be a list.' };
  }
  if (starts.length !== REQUIRED_SLOTS) {
    return { ok: false, error: `Pick exactly ${REQUIRED_SLOTS} time slots, ranked in order of preference.` };
  }
  if (new Set(starts).size !== starts.length) {
    return { ok: false, error: 'Your four time slots have to be four different windows.' };
  }

  const legal = new Map();
  for (const day of availableSlots(from)) {
    for (const slot of day.slots) legal.set(slot.start, slot);
  }

  const slots = [];
  for (const [index, raw] of starts.entries()) {
    const start = new Date(raw);
    if (Number.isNaN(start.getTime())) {
      return { ok: false, error: `Choice ${index + 1} is not a valid time.` };
    }
    const key = start.toISOString();
    const slot = legal.get(key);
    if (!slot) {
      // Separate the two ways a slot can be illegal, so the message is useful.
      const tooSoon = start < earliestStart(from);
      const tooFar = start > latestStart(from);
      const reason = tooSoon
        ? `it starts less than ${MIN_LEAD_HOURS} hours from now`
        : tooFar
          ? `it is more than ${MAX_WINDOW_DAYS} days away`
          : 'it is not one of our published windows (we run 8am, 11am, 2pm and 5pm, Monday to Saturday)';
      return { ok: false, error: `Choice ${index + 1} cannot be booked — ${reason}.` };
    }
    if (slot.full) {
      return { ok: false, error: `Choice ${index + 1} (${formatSlot(key)}) just filled up. Pick another window.` };
    }
    slots.push({
      preference: index + 1,
      start: key,
      end: new Date(start.getTime() + SLOT_HOURS * MS_HOUR).toISOString(),
    });
  }
  return { ok: true, slots };
}

export const SCHEDULING_RULES = {
  slotHours: SLOT_HOURS,
  requiredSlots: REQUIRED_SLOTS,
  minLeadHours: MIN_LEAD_HOURS,
  maxWindowDays: MAX_WINDOW_DAYS,
  timezone: SHOP_TIMEZONE,
};
