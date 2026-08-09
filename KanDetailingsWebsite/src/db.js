import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(HERE, '..', 'data');
fs.mkdirSync(DATA_DIR, { recursive: true });

export const db = new Database(process.env.KAN_DB || path.join(DATA_DIR, 'kan.db'));
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

db.exec(`
CREATE TABLE IF NOT EXISTS bookings (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  reference         TEXT    NOT NULL UNIQUE,   -- customer-facing code, e.g. KAN-7Q4T2M
  access_code       TEXT    NOT NULL,          -- second factor for opening the chat portal
  created_at        TEXT    NOT NULL,          -- ISO-8601 UTC, the "registration time"

  full_name         TEXT    NOT NULL,
  email             TEXT    NOT NULL,
  phone             TEXT    NOT NULL,

  address_line1     TEXT    NOT NULL,
  address_line2     TEXT,
  city              TEXT    NOT NULL,
  region            TEXT    NOT NULL,
  postal_code       TEXT    NOT NULL,
  address_notes     TEXT,

  vehicle_id        TEXT    NOT NULL,
  vehicle_brand     TEXT    NOT NULL,
  vehicle_make      TEXT    NOT NULL,
  vehicle_name      TEXT    NOT NULL,
  vehicle_year      INTEGER NOT NULL,
  vehicle_size      TEXT    NOT NULL,
  vehicle_colour    TEXT,
  plate             TEXT,

  package_id        TEXT    NOT NULL,
  package_label     TEXT    NOT NULL,
  addons            TEXT    NOT NULL DEFAULT '[]',  -- JSON array of add-on ids
  quoted_total      INTEGER NOT NULL,               -- whole currency units

  status            TEXT    NOT NULL DEFAULT 'pending',
  confirmed_slot    TEXT,                            -- ISO start of the slot staff locked in
  notes             TEXT,

  discord_thread_id TEXT
);

CREATE TABLE IF NOT EXISTS booking_slots (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id  INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  preference  INTEGER NOT NULL,   -- 1 = most preferred
  start_at    TEXT    NOT NULL,   -- ISO-8601 UTC
  end_at      TEXT    NOT NULL,
  UNIQUE (booking_id, preference)
);

CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id  INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  created_at  TEXT    NOT NULL,
  author_type TEXT    NOT NULL,   -- 'customer' | 'staff' | 'system'
  author_name TEXT    NOT NULL,
  body        TEXT    NOT NULL,
  source      TEXT    NOT NULL DEFAULT 'web',  -- 'web' | 'discord'
  discord_message_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_booking ON messages(booking_id, id);
CREATE INDEX IF NOT EXISTS idx_slots_booking    ON booking_slots(booking_id, preference);
CREATE INDEX IF NOT EXISTS idx_bookings_thread  ON bookings(discord_thread_id);

CREATE TABLE IF NOT EXISTS contact_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL,
  topic      TEXT NOT NULL,
  body       TEXT NOT NULL
);
`);

export function isoNow() {
  return new Date().toISOString();
}

const REF_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no I/O/0/1 — read aloud over the phone

function randomCode(length) {
  let out = '';
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  for (const b of bytes) out += REF_ALPHABET[b % REF_ALPHABET.length];
  return out;
}

export function newReference() {
  for (let attempt = 0; attempt < 20; attempt++) {
    const reference = `KAN-${randomCode(6)}`;
    const clash = db.prepare('SELECT 1 FROM bookings WHERE reference = ?').get(reference);
    if (!clash) return reference;
  }
  throw new Error('could not allocate a booking reference');
}

export function newAccessCode() {
  return randomCode(6);
}

/** A booking plus its slots and vehicle, shaped for the API and the Discord embeds. */
export function getBooking(id) {
  const row = db.prepare('SELECT * FROM bookings WHERE id = ?').get(id);
  return row ? hydrate(row) : null;
}

export function getBookingByReference(reference) {
  const row = db
    .prepare('SELECT * FROM bookings WHERE reference = ? COLLATE NOCASE')
    .get(String(reference || '').trim());
  return row ? hydrate(row) : null;
}

export function getBookingByThread(threadId) {
  const row = db.prepare('SELECT * FROM bookings WHERE discord_thread_id = ?').get(threadId);
  return row ? hydrate(row) : null;
}

export function listBookings({ status } = {}) {
  const rows = status
    ? db.prepare('SELECT * FROM bookings WHERE status = ? ORDER BY id DESC').all(status)
    : db.prepare('SELECT * FROM bookings ORDER BY id DESC').all();
  return rows.map(hydrate);
}

function hydrate(row) {
  const slots = db
    .prepare('SELECT preference, start_at, end_at FROM booking_slots WHERE booking_id = ? ORDER BY preference')
    .all(row.id);
  return {
    id: row.id,
    reference: row.reference,
    createdAt: row.created_at,
    status: row.status,
    confirmedSlot: row.confirmed_slot,
    notes: row.notes,
    discordThreadId: row.discord_thread_id,
    customer: {
      fullName: row.full_name,
      email: row.email,
      phone: row.phone,
    },
    address: {
      line1: row.address_line1,
      line2: row.address_line2,
      city: row.city,
      region: row.region,
      postalCode: row.postal_code,
      notes: row.address_notes,
    },
    vehicle: {
      id: row.vehicle_id,
      brand: row.vehicle_brand,
      make: row.vehicle_make,
      name: row.vehicle_name,
      year: row.vehicle_year,
      size: row.vehicle_size,
      colour: row.vehicle_colour,
      plate: row.plate,
    },
    service: {
      packageId: row.package_id,
      packageLabel: row.package_label,
      addons: JSON.parse(row.addons),
      quotedTotal: row.quoted_total,
    },
    slots: slots.map((s) => ({ preference: s.preference, start: s.start_at, end: s.end_at })),
  };
}

export function listMessages(bookingId, sinceId = 0) {
  return db
    .prepare(
      `SELECT id, created_at, author_type, author_name, body, source
         FROM messages
        WHERE booking_id = ? AND id > ?
        ORDER BY id`
    )
    .all(bookingId, sinceId)
    .map((m) => ({
      id: m.id,
      createdAt: m.created_at,
      authorType: m.author_type,
      authorName: m.author_name,
      body: m.body,
      source: m.source,
    }));
}

export function addMessage({ bookingId, authorType, authorName, body, source = 'web', discordMessageId = null }) {
  const info = db
    .prepare(
      `INSERT INTO messages (booking_id, created_at, author_type, author_name, body, source, discord_message_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .run(bookingId, isoNow(), authorType, authorName, body, source, discordMessageId);
  return db.prepare('SELECT * FROM messages WHERE id = ?').get(info.lastInsertRowid);
}

export function messageExistsFromDiscord(discordMessageId) {
  return !!db.prepare('SELECT 1 FROM messages WHERE discord_message_id = ?').get(discordMessageId);
}
