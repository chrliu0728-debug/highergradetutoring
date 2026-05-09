-- HigherGrade Tutoring — SQLite schema
-- Mirrors the entities previously kept in browser localStorage.
-- All blob/object fields (stats, roles, baseStats) are stored as JSON TEXT.

CREATE TABLE IF NOT EXISTS classes (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  classPoints     INTEGER NOT NULL DEFAULT 0,
  classBank       REAL    NOT NULL DEFAULT 0,
  bankLastUpdate  INTEGER,
  createdAt       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS students (
  id              TEXT PRIMARY KEY,
  firstName       TEXT,
  lastName        TEXT,
  studentEmail    TEXT,
  password        TEXT,
  parentEmail     TEXT,
  phone           TEXT,
  school          TEXT,
  grade           TEXT,
  classId         TEXT,
  className       TEXT,
  registeredAt    TEXT,
  stats           TEXT NOT NULL DEFAULT '{}',
  roles           TEXT NOT NULL DEFAULT '[]',
  baseStats       TEXT NOT NULL DEFAULT '{}',
  extras          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_students_email ON students(studentEmail);
CREATE INDEX IF NOT EXISTS idx_students_class ON students(classId);

CREATE TABLE IF NOT EXISTS base_stat_categories (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  icon            TEXT,
  pointsPerUnit   INTEGER NOT NULL DEFAULT 0,
  position        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS roles (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  icon            TEXT,
  color           TEXT,
  description     TEXT,
  special         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
  id              TEXT PRIMARY KEY,
  at              INTEGER NOT NULL,
  type            TEXT NOT NULL,
  scope           TEXT,
  subjectId       TEXT,
  subjectName     TEXT,
  relatedId       TEXT,
  relatedName     TEXT,
  amount          INTEGER,
  description     TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_at ON transactions(at);

CREATE TABLE IF NOT EXISTS staff (
  id              TEXT PRIMARY KEY,
  category        TEXT NOT NULL,
  name            TEXT,
  role            TEXT,
  image           TEXT,
  quote           TEXT,
  age             TEXT,
  school          TEXT,
  gender          TEXT,
  pronouns        TEXT,
  interests       TEXT,
  bio             TEXT,
  transcript      TEXT,
  transcriptFile  TEXT,           -- JSON: { data, name, type, size }
  position        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
  token           TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  studentId       TEXT,
  createdAt       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_kind ON sessions(kind);

-- Mini-game hints broadcast by admins; visible to crane-role holders.
CREATE TABLE IF NOT EXISTS hints (
  id          TEXT PRIMARY KEY,
  body        TEXT NOT NULL,
  createdAt   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hints_at ON hints(createdAt);

-- Generic key/value store for runtime feature flags (e.g. point-freeze).
CREATE TABLE IF NOT EXISTS meta (
  key         TEXT PRIMARY KEY,
  value       TEXT
);

-- Infinity-mode question bank (admin-managed, used by infinity.html).
-- `wrongAnswer` is the admin-chosen decoy that appears on the other door.
CREATE TABLE IF NOT EXISTS infinity_questions (
  id           TEXT PRIMARY KEY,
  question     TEXT NOT NULL,
  answer       TEXT NOT NULL,
  wrongAnswer  TEXT NOT NULL DEFAULT '',
  position     INTEGER NOT NULL DEFAULT 0,
  createdAt    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inf_pos ON infinity_questions(position);

-- Contact-form / sponsor-inquiry submissions (replaces the email-only flow).
CREATE TABLE IF NOT EXISTS contact_messages (
  id          TEXT PRIMARY KEY,
  createdAt   INTEGER NOT NULL,
  source      TEXT NOT NULL,           -- 'contact' | 'sponsor'
  type        TEXT,                    -- e.g. 'sponsor-gold', 'donate', 'other'
  name        TEXT,
  email       TEXT,
  org         TEXT,
  message     TEXT,
  isRead      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_at ON contact_messages(createdAt);

-- Discord-bot integration: maps a Discord user-id to a camp student.
-- One Discord user can only be linked to one student at a time, and
-- only one Discord user can claim a given student.
CREATE TABLE IF NOT EXISTS discord_links (
  discordId   TEXT PRIMARY KEY,
  studentId   TEXT NOT NULL UNIQUE,
  guildId     TEXT,
  linkedAt    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dl_student ON discord_links(studentId);

-- "Locked chests" placed in Discord by camp admins. Anyone who runs
-- /unlock with the right code receives the linked Discord role and the
-- chest's description as a reveal message. Multi-claim is allowed —
-- claimedBy is a JSON array of Discord user-ids that have unlocked it.
CREATE TABLE IF NOT EXISTS discord_chests (
  id           TEXT PRIMARY KEY,
  code         TEXT NOT NULL,
  description  TEXT,
  imageUrl     TEXT,
  roleId       TEXT NOT NULL,
  roleName     TEXT,
  guildId      TEXT NOT NULL,
  channelId    TEXT,
  messageId    TEXT,
  createdBy    TEXT,
  createdAt    INTEGER NOT NULL,
  claimedBy    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_chest_code ON discord_chests(guildId, code);

-- Per-guild slash-command permissions. If a (guildId, command) has at
-- least one row, only members of those roles (plus server owner +
-- Administrator) can run that command. If no rows exist for a command,
-- the bot defaults to "Administrator only".
CREATE TABLE IF NOT EXISTS discord_command_perms (
  id          TEXT PRIMARY KEY,
  guildId     TEXT NOT NULL,
  command     TEXT NOT NULL,
  roleId      TEXT NOT NULL,
  roleName    TEXT,
  createdBy   TEXT,
  createdAt   INTEGER NOT NULL,
  UNIQUE (guildId, command, roleId)
);
CREATE INDEX IF NOT EXISTS idx_dcp_guild_cmd ON discord_command_perms(guildId, command);

-- Discord roles whose grants should NOT be mirrored to the website's
-- per-student `roles` JSON. Server admins manage this list via
-- /role-mirror-block etc. Matching is case + whitespace insensitive on
-- the role name (the bot stores both id and name to handle renames).
CREATE TABLE IF NOT EXISTS discord_role_blocklist (
  id          TEXT PRIMARY KEY,
  guildId     TEXT NOT NULL,
  roleId      TEXT NOT NULL,
  roleName    TEXT,
  addedBy     TEXT,
  createdAt   INTEGER NOT NULL,
  UNIQUE (guildId, roleId)
);
CREATE INDEX IF NOT EXISTS idx_drb_guild ON discord_role_blocklist(guildId);

-- Camp registrations submitted from /register.html.
CREATE TABLE IF NOT EXISTS registrations (
  id                  TEXT PRIMARY KEY,
  createdAt           INTEGER NOT NULL,
  firstName           TEXT,
  lastName            TEXT,
  dob                 TEXT,
  studentEmail        TEXT,
  school              TEXT,
  parentFirst         TEXT,
  parentLast          TEXT,
  relationship        TEXT,
  parentPhone         TEXT,
  parentEmail         TEXT,
  emerg1Name          TEXT,
  emerg1Phone         TEXT,
  emerg1Relationship  TEXT,
  hobbies             TEXT,
  whyJoin             TEXT,
  consentPhoto        INTEGER NOT NULL DEFAULT 0,
  password            TEXT,
  waitlisted          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reg_at ON registrations(createdAt);
