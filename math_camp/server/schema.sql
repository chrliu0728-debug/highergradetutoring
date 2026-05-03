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
CREATE TABLE IF NOT EXISTS infinity_questions (
  id          TEXT PRIMARY KEY,
  question    TEXT NOT NULL,
  answer      TEXT NOT NULL,
  position    INTEGER NOT NULL DEFAULT 0,
  createdAt   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inf_pos ON infinity_questions(position);

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
  consentPhoto        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reg_at ON registrations(createdAt);
