import 'dotenv/config';

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';

import {
  addMessage,
  db,
  getBooking,
  getBookingByReference,
  getBookingByThread,
  isoNow,
  listBookings,
  listMessages,
  newAccessCode,
  newReference,
} from './src/db.js';
import { FACETS, MODELS, SIZE_TIERS, findVehicle } from './src/vehicles.js';
import { ADDONS, PACKAGES, findAddon, findPackage, quote } from './src/services.js';
import {
  SCHEDULING_RULES,
  availableSlots,
  formatSlot,
  validateSlotSelection,
} from './src/scheduling.js';
import { startDiscordBridge } from './src/discord.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 3000);
const STAFF_PASSWORD = process.env.STAFF_PASSWORD || 'change-me';

const app = express();
app.use(express.json({ limit: '64kb' }));
app.use(express.static(path.join(HERE, 'public'), { extensions: ['html'] }));

// ---------------------------------------------------------------- Discord ---

let discord = { enabled: false, announceBooking: async () => {}, relayCustomerMessage: async () => {}, relayStatusChange: async () => {} };

const setStatus = db.prepare('UPDATE bookings SET status = ? WHERE id = ?');
const setConfirmed = db.prepare('UPDATE bookings SET confirmed_slot = ?, status = ? WHERE id = ?');
const setThread = db.prepare('UPDATE bookings SET discord_thread_id = ? WHERE id = ?');

discord = await startDiscordBridge({
  store: {
    getBooking,
    getBookingByThread,
    setThreadId: (id, threadId) => setThread.run(threadId, id),
  },
  // Staff typed in the Discord thread -> land it in the website chat.
  onStaffMessage: ({ bookingId, authorName, body, discordMessageId }) => {
    addMessage({
      bookingId,
      authorType: 'staff',
      authorName,
      body,
      source: 'discord',
      discordMessageId,
    });
  },
  onCommand: (cmd) => {
    if (cmd.type === 'confirm') {
      setConfirmed.run(cmd.slot.start, 'confirmed', cmd.bookingId);
      addMessage({
        bookingId: cmd.bookingId,
        authorType: 'system',
        authorName: 'Kan Detailings',
        body: `Your appointment is confirmed for ${formatSlot(cmd.slot.start)} (choice #${cmd.slot.preference}).`,
        source: 'discord',
      });
    } else if (cmd.type === 'status') {
      setStatus.run(cmd.status, cmd.bookingId);
      addMessage({
        bookingId: cmd.bookingId,
        authorType: 'system',
        authorName: 'Kan Detailings',
        body: `Booking status updated to “${cmd.status}”.`,
        source: 'discord',
      });
    }
  },
});

// ------------------------------------------------------------- Public API ---

app.get('/api/catalog', (_req, res) => {
  res.json({
    models: MODELS,
    facets: FACETS,
    sizes: SIZE_TIERS,
  });
});

app.get('/api/services', (_req, res) => {
  res.json({ packages: PACKAGES, addons: ADDONS, sizes: SIZE_TIERS });
});

app.get('/api/slots', (_req, res) => {
  res.json({ rules: SCHEDULING_RULES, generatedAt: isoNow(), days: availableSlots() });
});

app.post('/api/quote', (req, res) => {
  const vehicle = findVehicle(req.body?.vehicleId);
  if (!vehicle) return res.status(400).json({ error: 'Pick a vehicle first.' });
  const result = quote({
    packageId: req.body?.packageId,
    addonIds: Array.isArray(req.body?.addons) ? req.body.addons : [],
    sizeMultiplier: SIZE_TIERS[vehicle.size]?.multiplier ?? 1,
  });
  if (!result) return res.status(400).json({ error: 'Pick a service package.' });
  res.json({ vehicle, quote: result });
});

const insertBooking = db.prepare(`
  INSERT INTO bookings (
    reference, access_code, created_at,
    full_name, email, phone,
    address_line1, address_line2, city, region, postal_code, address_notes,
    vehicle_id, vehicle_brand, vehicle_make, vehicle_name, vehicle_year, vehicle_size, vehicle_colour, plate,
    package_id, package_label, addons, quoted_total,
    status, notes
  ) VALUES (
    @reference, @access_code, @created_at,
    @full_name, @email, @phone,
    @address_line1, @address_line2, @city, @region, @postal_code, @address_notes,
    @vehicle_id, @vehicle_brand, @vehicle_make, @vehicle_name, @vehicle_year, @vehicle_size, @vehicle_colour, @plate,
    @package_id, @package_label, @addons, @quoted_total,
    'pending', @notes
  )
`);

const insertSlot = db.prepare(
  'INSERT INTO booking_slots (booking_id, preference, start_at, end_at) VALUES (?, ?, ?, ?)'
);

app.post('/api/bookings', async (req, res) => {
  const body = req.body || {};

  const text = (value, max = 200) => String(value ?? '').trim().slice(0, max);
  const required = {
    fullName: text(body.fullName, 120),
    email: text(body.email, 160),
    phone: text(body.phone, 40),
    line1: text(body.address?.line1, 160),
    city: text(body.address?.city, 80),
    region: text(body.address?.region, 80),
    postalCode: text(body.address?.postalCode, 20),
  };
  const missing = Object.entries(required)
    .filter(([, v]) => !v)
    .map(([k]) => k);
  if (missing.length) {
    return res.status(400).json({ error: `Missing required details: ${missing.join(', ')}.` });
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(required.email)) {
    return res.status(400).json({ error: 'That email address does not look right.' });
  }

  const vehicle = findVehicle(body.vehicleId);
  if (!vehicle) {
    return res.status(400).json({ error: 'Narrow the four filters down to a single vehicle before booking.' });
  }

  const pkg = findPackage(body.packageId);
  if (!pkg) return res.status(400).json({ error: 'Choose a service package.' });

  const addonIds = (Array.isArray(body.addons) ? body.addons : [])
    .map((id) => findAddon(id)?.id)
    .filter(Boolean);

  const slotCheck = validateSlotSelection(body.slots);
  if (!slotCheck.ok) return res.status(400).json({ error: slotCheck.error });

  const priced = quote({
    packageId: pkg.id,
    addonIds,
    sizeMultiplier: SIZE_TIERS[vehicle.size]?.multiplier ?? 1,
  });

  const reference = newReference();
  const accessCode = newAccessCode();

  const created = db.transaction(() => {
    const info = insertBooking.run({
      reference,
      access_code: accessCode,
      created_at: isoNow(),
      full_name: required.fullName,
      email: required.email,
      phone: required.phone,
      address_line1: required.line1,
      address_line2: text(body.address?.line2, 160) || null,
      city: required.city,
      region: required.region,
      postal_code: required.postalCode,
      address_notes: text(body.address?.notes, 500) || null,
      vehicle_id: vehicle.id,
      vehicle_brand: vehicle.brand,
      vehicle_make: vehicle.make,
      vehicle_name: vehicle.name,
      vehicle_year: vehicle.year,
      vehicle_size: vehicle.size,
      vehicle_colour: text(body.vehicleColour, 40) || null,
      plate: text(body.plate, 16) || null,
      package_id: pkg.id,
      package_label: pkg.label,
      addons: JSON.stringify(addonIds),
      quoted_total: priced.total,
      notes: text(body.notes, 1000) || null,
    });
    const bookingId = Number(info.lastInsertRowid);
    for (const slot of slotCheck.slots) {
      insertSlot.run(bookingId, slot.preference, slot.start, slot.end);
    }
    addMessage({
      bookingId,
      authorType: 'system',
      authorName: 'Kan Detailings',
      body:
        `Booking ${reference} received. We will confirm one of your four windows here shortly — ` +
        'reply in this thread any time and a detailer will pick it up.',
    });
    return bookingId;
  })();

  const booking = getBooking(created);
  discord.announceBooking(booking).catch((err) => console.error('[discord] announce failed:', err));

  res.status(201).json({
    reference,
    accessCode,
    booking: publicBooking(booking),
  });
});

// ---------------------------------------------------- Customer chat portal ---

function authCustomer(req, res) {
  const reference = String(req.params.reference || '').trim();
  const code = String(req.get('x-access-code') || req.query.code || req.body?.accessCode || '').trim();
  const row = db
    .prepare('SELECT id, access_code FROM bookings WHERE reference = ? COLLATE NOCASE')
    .get(reference);
  if (!row || row.access_code.toUpperCase() !== code.toUpperCase()) {
    res.status(404).json({ error: 'No booking matches that reference and access code.' });
    return null;
  }
  return getBooking(row.id);
}

app.get('/api/bookings/:reference', (req, res) => {
  const booking = authCustomer(req, res);
  if (!booking) return;
  res.json({ booking: publicBooking(booking) });
});

app.get('/api/bookings/:reference/messages', (req, res) => {
  const booking = authCustomer(req, res);
  if (!booking) return;
  res.json({
    status: booking.status,
    confirmedSlot: booking.confirmedSlot,
    messages: listMessages(booking.id, Number(req.query.since || 0)),
  });
});

app.post('/api/bookings/:reference/messages', async (req, res) => {
  const booking = authCustomer(req, res);
  if (!booking) return;
  const body = String(req.body?.body || '').trim().slice(0, 2000);
  if (!body) return res.status(400).json({ error: 'Write something first.' });

  const row = addMessage({
    bookingId: booking.id,
    authorType: 'customer',
    authorName: booking.customer.fullName,
    body,
  });
  const message = {
    id: row.id,
    createdAt: row.created_at,
    authorType: row.author_type,
    authorName: row.author_name,
    body: row.body,
    source: row.source,
  };
  discord.relayCustomerMessage(booking, message).catch((err) => console.error('[discord] relay failed:', err));
  res.status(201).json({ message });
});

// -------------------------------------------------------------- Staff API ---

function requireStaff(req, res, next) {
  const key = req.get('x-staff-key') || req.query.key || req.body?.key;
  if (key !== STAFF_PASSWORD) {
    return res.status(401).json({ error: 'Staff password is wrong.' });
  }
  next();
}

app.post('/api/staff/login', (req, res) => {
  if ((req.body?.password || '') !== STAFF_PASSWORD) {
    return res.status(401).json({ error: 'Staff password is wrong.' });
  }
  res.json({ ok: true });
});

app.get('/api/staff/bookings', requireStaff, (req, res) => {
  const bookings = listBookings({ status: req.query.status || undefined }).map((b) => ({
    ...publicBooking(b),
    customer: b.customer,
    address: b.address,
    unread: 0,
  }));
  res.json({ bookings, discord: discord.enabled });
});

app.get('/api/staff/bookings/:id/messages', requireStaff, (req, res) => {
  const booking = getBooking(Number(req.params.id));
  if (!booking) return res.status(404).json({ error: 'No such booking.' });
  res.json({
    status: booking.status,
    confirmedSlot: booking.confirmedSlot,
    messages: listMessages(booking.id, Number(req.query.since || 0)),
  });
});

app.post('/api/staff/bookings/:id/messages', requireStaff, (req, res) => {
  const booking = getBooking(Number(req.params.id));
  if (!booking) return res.status(404).json({ error: 'No such booking.' });
  const body = String(req.body?.body || '').trim().slice(0, 2000);
  if (!body) return res.status(400).json({ error: 'Write something first.' });
  const authorName = String(req.body?.authorName || 'Kan Detailings').trim().slice(0, 60);

  const row = addMessage({ bookingId: booking.id, authorType: 'staff', authorName, body });
  discord
    .relayStatusChange(booking, `**${authorName}** replied from the staff dashboard: ${body}`)
    .catch(() => {});
  res.status(201).json({
    message: {
      id: row.id,
      createdAt: row.created_at,
      authorType: row.author_type,
      authorName: row.author_name,
      body: row.body,
      source: row.source,
    },
  });
});

app.post('/api/staff/bookings/:id/confirm', requireStaff, (req, res) => {
  const booking = getBooking(Number(req.params.id));
  if (!booking) return res.status(404).json({ error: 'No such booking.' });
  const preference = Number(req.body?.preference);
  const slot = booking.slots.find((s) => s.preference === preference);
  if (!slot) return res.status(400).json({ error: 'That is not one of their four choices.' });

  setConfirmed.run(slot.start, 'confirmed', booking.id);
  addMessage({
    bookingId: booking.id,
    authorType: 'system',
    authorName: 'Kan Detailings',
    body: `Your appointment is confirmed for ${formatSlot(slot.start)} (choice #${preference}).`,
  });
  discord.relayStatusChange(booking, `Confirmed choice #${preference} — ${formatSlot(slot.start)}.`).catch(() => {});
  res.json({ booking: publicBooking(getBooking(booking.id)) });
});

app.post('/api/staff/bookings/:id/status', requireStaff, (req, res) => {
  const booking = getBooking(Number(req.params.id));
  if (!booking) return res.status(404).json({ error: 'No such booking.' });
  const status = String(req.body?.status || '').toLowerCase();
  if (!['pending', 'confirmed', 'in_progress', 'completed', 'cancelled'].includes(status)) {
    return res.status(400).json({ error: 'Unknown status.' });
  }
  setStatus.run(status, booking.id);
  addMessage({
    bookingId: booking.id,
    authorType: 'system',
    authorName: 'Kan Detailings',
    body: `Booking status updated to “${status}”.`,
  });
  discord.relayStatusChange(booking, `Status changed to **${status}**.`).catch(() => {});
  res.json({ booking: publicBooking(getBooking(booking.id)) });
});

// ------------------------------------------------------------- Contact us ---

app.post('/api/contact', (req, res) => {
  const name = String(req.body?.name || '').trim().slice(0, 120);
  const email = String(req.body?.email || '').trim().slice(0, 160);
  const topic = String(req.body?.topic || 'general').trim().slice(0, 60);
  const body = String(req.body?.body || '').trim().slice(0, 4000);
  if (!name || !email || !body) {
    return res.status(400).json({ error: 'Name, email and a message are all required.' });
  }
  db.prepare('INSERT INTO contact_messages (created_at, name, email, topic, body) VALUES (?, ?, ?, ?, ?)').run(
    isoNow(),
    name,
    email,
    topic,
    body
  );
  res.status(201).json({ ok: true });
});

// ------------------------------------------------------------------ Utils ---

/** A booking with the access code and other internals stripped out. */
function publicBooking(booking) {
  return {
    id: booking.id,
    reference: booking.reference,
    createdAt: booking.createdAt,
    status: booking.status,
    confirmedSlot: booking.confirmedSlot,
    confirmedSlotLabel: booking.confirmedSlot ? formatSlot(booking.confirmedSlot) : null,
    vehicle: booking.vehicle,
    service: booking.service,
    notes: booking.notes,
    slots: booking.slots.map((s) => ({ ...s, label: formatSlot(s.start) })),
  };
}

app.use((err, _req, res, _next) => {
  console.error('[server]', err);
  res.status(500).json({ error: 'Something broke on our end. Try again in a moment.' });
});

app.listen(PORT, () => {
  console.log(`Kan Detailings running on http://localhost:${PORT}`);
  console.log(`Timezone: ${SCHEDULING_RULES.timezone} · Discord bridge: ${discord.enabled ? 'on' : 'off'}`);
});
