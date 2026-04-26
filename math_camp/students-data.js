/* ============================================================
   STUDENT DATA — HigherGrade Tutoring  (server-backed)
   ----------------------------------------------------------------
   The site previously stored everything in localStorage. It now
   talks to a Flask + SQLite backend at /api/*. Each page should
   `await window.dataReady` before using any of the read helpers
   below — the helpers read from in-memory caches that are filled
   by the bootstrap fetch.

   API surface is preserved so most call-sites continue to work:
   getStudents() / getClasses() / getRoles() / getTransactions()
   / getBaseStatCategories() return synchronously from cache.
   Mutations are async — they call the server, then refresh
   the cache. Existing code that calls them without `await` will
   still appear to work optimistically (the cache is updated
   synchronously before the network round-trip).
   ============================================================ */

const STAT_FIELDS = [
  { key: 'privatePoints',     label: 'Current Points',    icon: '💰', short: 'Current' },
  { key: 'totalPointsEarned', label: 'Total Points',      icon: '🏆', short: 'Total',  rankKey: true },
  { key: 'luck',              label: 'Luck',              icon: '🍀', short: 'Luck' },
  { key: 'perfectScores',     label: '100% Scores',       icon: '⭐', short: 'Perfect' },
  { key: 'classAnswers',      label: 'Class Answers',     icon: '✋', short: 'Answers' },
  { key: 'pointExchanges',    label: 'Point Exchanges',   icon: '🔄', short: 'Exchanges' },
  { key: 'bathroomVisits',    label: 'Bathroom Visits',   icon: '🚽', short: 'Bathroom' },
  { key: 'badWords',          label: 'Bad Words Caught',  icon: '🤬', short: 'Bad Words' },
];

const CLICKER_META_DEFAULTS = {
  clickerClicks: 0,
  clickerPointsEarned: 0,
  spiderShown: false,
};

const LUCK_COST = 800;
const CLICKER_RATE = 100;
const CLICKER_COOLDOWN_MS = 60;
const TRANSFER_KEEP_RATIO = 0.5;
const SPIDER_THRESHOLD = 20;

function defaultStats() {
  const o = {};
  STAT_FIELDS.forEach(f => { o[f.key] = 0; });
  Object.assign(o, CLICKER_META_DEFAULTS);
  return o;
}

function newStudentId() {
  return 'student-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
}

/* ──────────────────────────────────────────────────────────────
   In-memory caches. Populated by HG.bootstrap() (see bottom).
   ────────────────────────────────────────────────────────────── */
const HG = (window.HG = window.HG || {
  cache: {
    students: [],
    classes: [],
    roles: [],
    baseStats: [],
    transactions: [],
    staff: [],
    me: null,         // { kind, student }
  },
  ready: false,
});

async function _api(path, opts = {}) {
  const init = {
    method: opts.method || 'GET',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const res = await fetch('/api' + path, init);
  let data = null;
  try { data = await res.json(); } catch (_) { /* may be 204 */ }
  if (!res.ok) {
    const msg = (data && data.error) || ('HTTP ' + res.status);
    const err = new Error(msg); err.status = res.status; err.data = data;
    throw err;
  }
  return data || { ok: true };
}

async function _refresh(key, path, payloadKey) {
  try {
    const r = await _api(path);
    HG.cache[key] = r.data || [];
    return HG.cache[key];
  } catch (e) {
    console.warn('refresh', key, 'failed:', e.message);
    return HG.cache[key];
  }
}

async function _bootstrap() {
  // Fire all reads in parallel.
  const [me, students, classes, roles, baseStats, txs, staff] = await Promise.all([
    _api('/auth/me').catch(() => ({ kind: null, student: null })),
    _api('/students').catch(() => ({ data: [] })),
    _api('/classes').catch(() => ({ data: [] })),
    _api('/roles').catch(() => ({ data: [] })),
    _api('/base-stats').catch(() => ({ data: [] })),
    _api('/transactions').catch(() => ({ data: [] })),
    _api('/staff').catch(() => ({ data: [] })),
  ]);
  HG.cache.me            = { kind: me.kind || null, student: me.student || null };
  HG.cache.students      = students.data || [];
  HG.cache.classes       = classes.data || [];
  HG.cache.roles         = roles.data || [];
  HG.cache.baseStats     = baseStats.data || [];
  HG.cache.transactions  = txs.data || [];
  HG.cache.staff         = staff.data || [];
  HG.ready = true;
}

window.dataReady = _bootstrap();
HG.refresh = function () {
  // Re-runs the bootstrap fetch + replaces window.dataReady so callers
  // that `await window.dataReady` after a successful login/unlock pick
  // up freshly-fetched data (e.g. admins seeing redacted fields).
  window.dataReady = _bootstrap();
  return window.dataReady;
};

/* ──────────────────────────────────────────────────────────────
   Students — sync reads from cache, async writes to server.
   ────────────────────────────────────────────────────────────── */
function getStudents() {
  return HG.cache.students.map(s => ({
    ...s,
    stats: { ...defaultStats(), ...(s.stats || {}) },
  }));
}

async function saveStudents(arr) {
  // Bulk-replace path used by all admin-side mutations that compute
  // a new students array client-side and want to persist it.
  HG.cache.students = arr;
  await _api('/students', { method: 'PUT', body: { students: arr } });
}

async function addStudent(student) {
  HG.cache.students.push(student);
  await _api('/students', { method: 'POST', body: student });
}

async function updateStudent(id, updates) {
  const idx = HG.cache.students.findIndex(s => s.id === id);
  if (idx < 0) return false;
  HG.cache.students[idx] = {
    ...HG.cache.students[idx],
    ...updates,
    stats: { ...HG.cache.students[idx].stats, ...(updates.stats || {}) },
  };
  await saveStudents(HG.cache.students);
  return true;
}

async function deleteStudent(id) {
  HG.cache.students = HG.cache.students.filter(s => s.id !== id);
  await _api('/students/' + encodeURIComponent(id), { method: 'DELETE' });
}

function sortedByTotalPoints(students) {
  return students.slice().sort((a, b) => {
    const at = (a.stats && a.stats.totalPointsEarned) || 0;
    const bt = (b.stats && b.stats.totalPointsEarned) || 0;
    if (bt !== at) return bt - at;
    const ap = (a.stats && a.stats.privatePoints) || 0;
    const bp = (b.stats && b.stats.privatePoints) || 0;
    if (bp !== ap) return bp - ap;
    return (a.id || '').localeCompare(b.id || '');
  });
}

function studentFullName(s) {
  return [s.firstName, s.lastName].filter(Boolean).join(' ').trim() || '(no name)';
}

/* ──────────────────────────────────────────────────────────────
   Base stat categories
   ────────────────────────────────────────────────────────────── */
function getBaseStatCategories() {
  return HG.cache.baseStats.slice();
}

async function saveBaseStatCategories(arr) {
  HG.cache.baseStats = arr;
  await _api('/base-stats', { method: 'PUT', body: { baseStats: arr } });
}

async function resetBaseStatCategories() {
  // re-seed defaults via DELETE-then-PUT-empty, then refresh.
  HG.cache.baseStats = [];
  await _api('/base-stats', { method: 'PUT', body: { baseStats: [] } });
  await _refresh('baseStats', '/base-stats');
}

function newBaseStatId() {
  return 'bs-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
}

async function updateStudentBaseStat(studentId, catId, value) {
  const s = HG.cache.students.find(x => x.id === studentId);
  if (!s) return false;
  s.baseStats = s.baseStats || {};
  s.baseStats[catId] = Math.max(0, parseInt(value, 10) || 0);
  await saveStudents(HG.cache.students);
  return true;
}

function studentBaseStatTotal(student) {
  const cats = getBaseStatCategories();
  const vals = (student && student.baseStats) || {};
  let total = 0;
  cats.forEach(c => {
    total += (parseInt(vals[c.id], 10) || 0) * (parseInt(c.pointsPerUnit, 10) || 0);
  });
  return total;
}

/* ──────────────────────────────────────────────────────────────
   Auth — server-side cookie session.
   ────────────────────────────────────────────────────────────── */
async function findStudentByLogin(email, password) {
  try {
    const r = await _api('/auth/student/login', {
      method: 'POST', body: { email, password },
    });
    if (r && r.ok && r.student) {
      HG.cache.me = { kind: 'student', student: r.student };
      // Make sure new student appears in the cache too.
      const idx = HG.cache.students.findIndex(s => s.id === r.student.id);
      if (idx >= 0) HG.cache.students[idx] = r.student;
      else HG.cache.students.push(r.student);
      return r.student;
    }
  } catch (_) { /* fallthrough → null */ }
  return null;
}

function getLoggedInStudent() {
  return (HG.cache.me && HG.cache.me.student) || null;
}

async function setLoggedInStudent(id) {
  // The server is the source of truth; this helper exists for
  // call-sites that want to toggle the session manually.
  if (!id) {
    await _api('/auth/student/logout', { method: 'POST' });
    HG.cache.me = { kind: null, student: null };
    return;
  }
  // We can't impersonate a student without their password, so
  // this branch only refreshes /auth/me.
  const me = await _api('/auth/me');
  HG.cache.me = { kind: me.kind || null, student: me.student || null };
}

/* ──────────────────────────────────────────────────────────────
   Transactions
   ────────────────────────────────────────────────────────────── */
const TX_MAX = 2000;
const TX_TYPES = {
  earn:                { label: 'Admin awarded',        icon: '💰' },
  spend:               { label: 'Marked as spent',      icon: '💸' },
  penalty:             { label: 'Penalty',              icon: '⚠️' },
  curse:               { label: 'Curse-word penalty',   icon: '🤬' },
  transfer_out:        { label: 'Transfer sent',        icon: '📤' },
  transfer_in:         { label: 'Transfer received',    icon: '📥' },
  luck:                { label: 'Invested in luck',     icon: '🍀' },
  clicker:             { label: 'Clicker earn',         icon: '🖱' },
  class_award:         { label: 'Class pts awarded',    icon: '🌟' },
  class_claim:         { label: 'Class pts claimed',    icon: '✅' },
  class_bank_deposit:  { label: 'Class bank deposit',   icon: '🏦' },
  class_bank_withdraw: { label: 'Class bank withdraw',  icon: '↩' },
  class_bank_adjust:   { label: 'Class bank adjustment',icon: '🏦' },
};

function getTransactions() {
  return HG.cache.transactions.slice();
}

async function saveTransactions(arr) {
  if (arr.length > TX_MAX) arr = arr.slice(-TX_MAX);
  HG.cache.transactions = arr;
  await _api('/transactions', { method: 'PUT', body: { transactions: arr } });
}

async function clearTransactions() {
  HG.cache.transactions = [];
  await _api('/transactions', { method: 'DELETE' });
}

function logTransaction(entry) {
  const arr = getTransactions();
  arr.push({
    id: 'tx-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
    at: Date.now(),
    ...entry,
  });
  // Fire-and-forget; cache is updated synchronously inside saveTransactions.
  saveTransactions(arr);
}

/* ──────────────────────────────────────────────────────────────
   FULL DEV RESET — wipe student-side state on the server.
   ────────────────────────────────────────────────────────────── */
async function devResetEverything() {
  await _api('/admin/reset', { method: 'POST' });
  // Clear any stale browser session keys
  try { sessionStorage.removeItem('highergrade_admin_unlocked'); } catch (_) {}
  try { sessionStorage.removeItem('highergrade_signin_clicks'); } catch (_) {}
  // Refresh caches
  await _bootstrap();
}

/* ──────────────────────────────────────────────────────────────
   Roles
   ────────────────────────────────────────────────────────────── */
const MAZEWIZ_ROLE_ID = 'mazewiz';
const MAZEWIZ_PASSCODE = 'MazeWiz';

function getRoles() {
  // Guarantee the protected MazeWiz role always shows
  const arr = HG.cache.roles.slice();
  if (!arr.some(r => r.id === MAZEWIZ_ROLE_ID)) {
    arr.unshift({
      id: MAZEWIZ_ROLE_ID,
      name: 'Maze Wizard',
      icon: '🧙',
      color: '#8B5CF6',
      description: 'The first student from their class to find the hidden staff sign-in page.',
      special: true,
    });
  }
  return arr;
}

async function saveRoles(arr) {
  HG.cache.roles = arr;
  await _api('/roles', { method: 'PUT', body: { roles: arr } });
}

function newRoleId() {
  return 'role-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
}

async function addRole(role) {
  const arr = getRoles();
  if (arr.some(r => r.id === role.id || r.name.toLowerCase() === (role.name || '').toLowerCase())) {
    return { ok: false, error: 'A role with that ID or name already exists.' };
  }
  arr.push(role);
  await saveRoles(arr);
  return { ok: true };
}

async function updateRole(id, updates) {
  const arr = getRoles();
  const idx = arr.findIndex(r => r.id === id);
  if (idx < 0) return false;
  arr[idx] = { ...arr[idx], ...updates };
  await saveRoles(arr);
  return true;
}

async function deleteRole(id) {
  if (id === MAZEWIZ_ROLE_ID) return false;
  const arr = getRoles().filter(r => r.id !== id);
  await saveRoles(arr);
  // Strip from every student
  const students = getStudents();
  let changed = false;
  students.forEach(s => {
    if (Array.isArray(s.roles) && s.roles.includes(id)) {
      s.roles = s.roles.filter(x => x !== id);
      changed = true;
    }
  });
  if (changed) await saveStudents(students);
  return true;
}

async function assignRoleToStudent(studentId, roleId) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return false;
  s.roles = Array.isArray(s.roles) ? s.roles : [];
  if (!s.roles.includes(roleId)) s.roles.push(roleId);
  await saveStudents(students);
  return true;
}

async function removeRoleFromStudent(studentId, roleId) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s || !Array.isArray(s.roles)) return false;
  s.roles = s.roles.filter(x => x !== roleId);
  await saveStudents(students);
  return true;
}

function studentHasRole(student, roleId) {
  return !!(student && Array.isArray(student.roles) && student.roles.includes(roleId));
}

function getMazeWizWinnerForClass(classId) {
  if (!classId) return null;
  const students = getStudents();
  return students.find(s => s.classId === classId && studentHasRole(s, MAZEWIZ_ROLE_ID)) || null;
}

async function claimMazeWiz(studentId) {
  // Server-side validated; current student must be logged in.
  try {
    const r = await _api('/students/me/mazewiz', { method: 'POST' });
    // Refresh students cache so the new role shows up
    await _refresh('students', '/students');
    return r;
  } catch (e) {
    return { ok: false, error: e.message || 'Could not claim the title.' };
  }
}

/* ──────────────────────────────────────────────────────────────
   Student-side actions  (all server-validated)
   ────────────────────────────────────────────────────────────── */
async function investInLuck(_studentId) {
  try {
    const r = await _api('/students/me/luck', { method: 'POST' });
    await _refresh('students', '/students');
    await _refresh('transactions', '/transactions');
    return r;
  } catch (e) {
    return { ok: false, error: e.message || 'Could not invest.' };
  }
}

async function transferPoints(_fromId, toId, amount) {
  try {
    const r = await _api('/students/me/transfer', { method: 'POST', body: { toId, amount } });
    await _refresh('students', '/students');
    await _refresh('transactions', '/transactions');
    return r;
  } catch (e) {
    return { ok: false, error: e.message || 'Transfer failed.' };
  }
}

async function clickerTap(_studentId) {
  try {
    const r = await _api('/students/me/click', { method: 'POST' });
    // Patch the cache locally to avoid a round-trip per click
    const me = HG.cache.me && HG.cache.me.student;
    if (me) {
      me.stats = me.stats || defaultStats();
      me.stats.clickerClicks = r.data.clicks;
      me.stats.clickerPointsEarned = r.data.clickerPointsEarned;
      if (r.data.earned) {
        me.stats.privatePoints = (me.stats.privatePoints || 0) + r.data.earned;
        me.stats.totalPointsEarned = (me.stats.totalPointsEarned || 0) + r.data.earned;
      }
      const idx = HG.cache.students.findIndex(s => s.id === me.id);
      if (idx >= 0) HG.cache.students[idx] = { ...HG.cache.students[idx], stats: me.stats };
    }
    return r;
  } catch (e) {
    return { ok: false, error: e.message || 'Click failed.' };
  }
}

/* Admin: overwrite arbitrary top-level fields on a student */
async function updateStudentProfile(id, updates) {
  const idx = HG.cache.students.findIndex(s => s.id === id);
  if (idx < 0) return false;
  const current = HG.cache.students[idx];
  const statsUpdate = updates.stats;
  const top = { ...updates };
  delete top.stats;
  HG.cache.students[idx] = { ...current, ...top };
  if (statsUpdate) HG.cache.students[idx].stats = { ...current.stats, ...statsUpdate };
  await saveStudents(HG.cache.students);
  return true;
}

async function applyPenalty(studentId, amount) {
  amount = parseInt(amount, 10) || 0;
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return { ok: false, error: 'Student not found.' };
  s.stats = { ...defaultStats(), ...(s.stats || {}) };
  s.stats.privatePoints = Math.max(0, (s.stats.privatePoints || 0) - amount);
  await saveStudents(students);
  logTransaction({
    type: 'penalty', scope: 'student',
    subjectId: studentId, subjectName: studentFullName(s),
    amount: -amount, description: `Admin penalty −${amount} pts`,
  });
  return { ok: true, data: { amount, remaining: s.stats.privatePoints } };
}

async function applyCursePenalty(studentId, amount) {
  amount = parseInt(amount, 10) || 0;
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return { ok: false, error: 'Student not found.' };
  s.stats = { ...defaultStats(), ...(s.stats || {}) };
  s.stats.privatePoints = Math.max(0, (s.stats.privatePoints || 0) - amount);
  s.stats.badWords = (s.stats.badWords || 0) + 1;
  await saveStudents(students);
  logTransaction({
    type: 'curse', scope: 'student',
    subjectId: studentId, subjectName: studentFullName(s),
    amount: -amount,
    description: `Curse-word penalty −${amount} pts (bad-word count +1)`,
  });
  return { ok: true, data: { amount, remaining: s.stats.privatePoints, badWords: s.stats.badWords } };
}

/* ──────────────────────────────────────────────────────────────
   Classes
   ────────────────────────────────────────────────────────────── */
const CLASS_POINT_TO_INDIVIDUAL = 10;
const CLASS_BANK_DAILY_RATE = 0.05;

function getClasses() {
  return HG.cache.classes.slice();
}

async function saveClasses(arr) {
  HG.cache.classes = arr;
  await _api('/classes', { method: 'PUT', body: { classes: arr } });
}

function newClassId() {
  return 'class-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
}

function getClassById(id) {
  if (!id) return null;
  return getClasses().find(c => c.id === id) || null;
}

function findClassMembers(classId) {
  if (!classId) return [];
  return getStudents().filter(s => s.classId === classId);
}

async function addClass(name) {
  name = String(name || '').trim();
  if (!name) return { ok: false, error: 'Class needs a name.' };
  const classes = getClasses();
  if (classes.some(c => c.name.toLowerCase() === name.toLowerCase())) {
    return { ok: false, error: `A class named "${name}" already exists.` };
  }
  const cls = {
    id: newClassId(), name,
    classPoints: 0, classBank: 0,
    createdAt: new Date().toISOString(),
  };
  classes.push(cls);
  await saveClasses(classes);
  return { ok: true, data: cls };
}

async function updateClass(id, updates) {
  const classes = getClasses();
  const idx = classes.findIndex(c => c.id === id);
  if (idx < 0) return false;
  classes[idx] = { ...classes[idx], ...updates };
  await saveClasses(classes);
  if (updates.name) {
    const students = getStudents();
    let changed = false;
    students.forEach(s => {
      if (s.classId === id) { s.className = updates.name; changed = true; }
    });
    if (changed) await saveStudents(students);
  }
  return true;
}

async function deleteClass(id) {
  await saveClasses(getClasses().filter(c => c.id !== id));
  const students = getStudents();
  let changed = false;
  students.forEach(s => {
    if (s.classId === id) { s.classId = null; s.className = ''; changed = true; }
  });
  if (changed) await saveStudents(students);
}

async function assignStudentToClass(studentId, classId) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return false;
  if (!classId) {
    s.classId = null; s.className = '';
  } else {
    const cls = getClassById(classId);
    s.classId = classId; s.className = cls ? cls.name : '';
  }
  await saveStudents(students);
  return true;
}

function refreshClassBank(cls) {
  if (!cls) return;
  const now = Date.now();
  if (!cls.bankLastUpdate) { cls.bankLastUpdate = now; return; }
  if (!cls.classBank || cls.classBank <= 0) { cls.bankLastUpdate = now; return; }
  const days = (now - cls.bankLastUpdate) / (1000 * 60 * 60 * 24);
  if (days <= 0) return;
  cls.classBank = cls.classBank * Math.pow(1 + CLASS_BANK_DAILY_RATE, days);
  cls.bankLastUpdate = now;
}

function getClassesWithBankRefresh() {
  const classes = getClasses();
  let dirty = false;
  classes.forEach(cls => {
    const before = cls.classBank;
    refreshClassBank(cls);
    if (cls.classBank !== before) dirty = true;
  });
  if (dirty) saveClasses(classes); // fire-and-forget
  return classes;
}

async function awardClassPoints(classId, delta) {
  delta = parseInt(delta, 10) || 0;
  if (delta === 0) return { ok: false, error: 'Amount cannot be zero.' };
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  const newPool = Math.max(0, (cls.classPoints || 0) + delta);
  const actual = newPool - (cls.classPoints || 0);
  cls.classPoints = newPool;
  await saveClasses(classes);
  logTransaction({
    type: 'class_award', scope: 'class',
    subjectId: classId, subjectName: cls.name, amount: actual,
    description: `${actual >= 0 ? '+' : ''}${actual} class pt${Math.abs(actual) === 1 ? '' : 's'} · unclaimed pool now ${newPool}`,
  });
  return { ok: true, data: { delta: actual, unclaimed: newPool } };
}

async function claimClassPoints(classId, amount) {
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  const available = cls.classPoints || 0;
  if (available <= 0) return { ok: false, error: 'No unclaimed class points to claim.' };
  amount = (amount == null) ? available : (parseInt(amount, 10) || 0);
  if (amount <= 0) return { ok: false, error: 'Amount must be positive.' };
  if (amount > available) amount = available;

  cls.classPoints = available - amount;
  await saveClasses(classes);

  const perMember = amount * CLASS_POINT_TO_INDIVIDUAL;
  const students = getStudents();
  let memberCount = 0;
  students.forEach(s => {
    if (s.classId === classId) {
      memberCount++;
      s.stats = { ...defaultStats(), ...(s.stats || {}) };
      s.stats.privatePoints     = (s.stats.privatePoints || 0) + perMember;
      s.stats.totalPointsEarned = (s.stats.totalPointsEarned || 0) + perMember;
    }
  });
  await saveStudents(students);
  logTransaction({
    type: 'class_claim', scope: 'class',
    subjectId: classId, subjectName: cls.name, amount: -amount,
    description: `Claimed ${amount} class pt${amount === 1 ? '' : 's'} · distributed ${perMember * memberCount} pts across ${memberCount} member${memberCount === 1 ? '' : 's'}`,
  });
  return { ok: true, data: { claimed: amount, perMember, memberCount, totalDistributed: perMember * memberCount } };
}

async function bankClassPoints(classId, amount) {
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  refreshClassBank(cls);
  const available = cls.classPoints || 0;
  if (available <= 0) return { ok: false, error: 'No unclaimed class points to bank.' };
  amount = (amount == null) ? available : (parseInt(amount, 10) || 0);
  if (amount <= 0) return { ok: false, error: 'Amount must be positive.' };
  if (amount > available) amount = available;
  cls.classPoints = available - amount;
  cls.classBank   = (cls.classBank || 0) + amount;
  cls.bankLastUpdate = Date.now();
  await saveClasses(classes);
  logTransaction({
    type: 'class_bank_deposit', scope: 'class',
    subjectId: classId, subjectName: cls.name, amount,
    description: `Deposited ${amount} class pt${amount === 1 ? '' : 's'} to bank · new balance ${cls.classBank.toFixed(2)}`,
  });
  return { ok: true, data: { banked: amount, newBank: cls.classBank } };
}

async function withdrawFromBank(classId, amount) {
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  refreshClassBank(cls);
  amount = (amount == null) ? Math.floor(cls.classBank || 0) : (parseInt(amount, 10) || 0);
  if (amount <= 0) return { ok: false, error: 'Amount must be positive.' };
  if ((cls.classBank || 0) < amount) {
    return { ok: false, error: `Bank has only ${Math.floor(cls.classBank || 0)} points available.` };
  }
  cls.classBank   = (cls.classBank || 0) - amount;
  cls.classPoints = (cls.classPoints || 0) + amount;
  await saveClasses(classes);
  logTransaction({
    type: 'class_bank_withdraw', scope: 'class',
    subjectId: classId, subjectName: cls.name, amount: -amount,
    description: `Withdrew ${amount} pts from bank to unclaimed pool`,
  });
  return { ok: true, data: { withdrawn: amount, newBank: cls.classBank, newUnclaimed: cls.classPoints } };
}

async function adjustClassBank(classId, delta) {
  delta = parseInt(delta, 10) || 0;
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  refreshClassBank(cls);
  cls.classBank = Math.max(0, (cls.classBank || 0) + delta);
  cls.bankLastUpdate = Date.now();
  await saveClasses(classes);
  logTransaction({
    type: 'class_bank_adjust', scope: 'class',
    subjectId: classId, subjectName: cls.name, amount: delta,
    description: `Admin bank adjustment ${delta >= 0 ? '+' : ''}${delta} · new balance ${cls.classBank.toFixed(2)}`,
  });
  return { ok: true, data: { newBalance: cls.classBank } };
}
