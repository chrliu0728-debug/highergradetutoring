/* ============================================================
   STUDENT DATA — HigherGrade Tutoring
   Shared by register.html, leaderboard.html, and admin-students.html.
   ============================================================ */

const STUDENT_STORAGE_KEY = 'highergrade_students_v1';

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

// Meta fields (not shown in STAT_FIELDS UI but tracked on stats object)
const CLICKER_META_DEFAULTS = {
  clickerClicks: 0,
  clickerPointsEarned: 0,
  spiderShown: false,
};

const LUCK_COST = 800;       // points required per luck point
const CLICKER_RATE = 100;    // clicks per 1 earned point
const CLICKER_COOLDOWN_MS = 60; // 0.06s between clicks
const TRANSFER_KEEP_RATIO = 0.5; // recipient keeps 50% of transfer amount
const SPIDER_THRESHOLD = 20; // clicker points to trigger spider

function defaultStats() {
  const o = {};
  STAT_FIELDS.forEach(f => { o[f.key] = 0; });
  Object.assign(o, CLICKER_META_DEFAULTS);
  return o;
}

function newStudentId() {
  return 'student-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
}

function getStudents() {
  try {
    const stored = localStorage.getItem(STUDENT_STORAGE_KEY);
    if (stored) {
      const arr = JSON.parse(stored);
      if (Array.isArray(arr)) {
        // Backfill missing stat fields on older records
        return arr.map(s => ({
          ...s,
          stats: { ...defaultStats(), ...(s.stats || {}) },
        }));
      }
    }
  } catch (e) { /* fall through */ }
  return [];
}

function saveStudents(arr) {
  localStorage.setItem(STUDENT_STORAGE_KEY, JSON.stringify(arr));
}

function addStudent(student) {
  const students = getStudents();
  students.push(student);
  saveStudents(students);
}

function updateStudent(id, updates) {
  const students = getStudents();
  const idx = students.findIndex(s => s.id === id);
  if (idx < 0) return false;
  students[idx] = { ...students[idx], ...updates, stats: { ...students[idx].stats, ...(updates.stats || {}) } };
  saveStudents(students);
  return true;
}

function deleteStudent(id) {
  saveStudents(getStudents().filter(s => s.id !== id));
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

/* ============================================================
   Base Stat Categories — global list, admin-managed.
   Each student stores per-category counts in student.baseStats.
   Points earned = sum of (count × pointsPerUnit) across categories.
   These are SEPARATE from the admin-tracked leaderboard stats.
   ============================================================ */

const BASE_STAT_STORAGE_KEY = 'highergrade_base_stats_v1';

const DEFAULT_BASE_STATS = [
  { id: 'homework',  name: 'Homework Pages',    icon: '📚', pointsPerUnit: 5 },
  { id: 'practice',  name: 'Practice Problems', icon: '📝', pointsPerUnit: 2 },
  { id: 'reading',   name: 'Reading Minutes',   icon: '📖', pointsPerUnit: 1 },
];

function getBaseStatCategories() {
  try {
    const stored = localStorage.getItem(BASE_STAT_STORAGE_KEY);
    if (stored) {
      const arr = JSON.parse(stored);
      if (Array.isArray(arr)) return arr;
    }
  } catch (e) { /* fall through */ }
  return DEFAULT_BASE_STATS.slice();
}

function saveBaseStatCategories(arr) {
  localStorage.setItem(BASE_STAT_STORAGE_KEY, JSON.stringify(arr));
}

function resetBaseStatCategories() {
  localStorage.removeItem(BASE_STAT_STORAGE_KEY);
}

function newBaseStatId() {
  return 'bs-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
}

function updateStudentBaseStat(studentId, catId, value) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return false;
  s.baseStats = s.baseStats || {};
  s.baseStats[catId] = Math.max(0, parseInt(value, 10) || 0);
  saveStudents(students);
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

/* ============================================================
   Student login — plain-text password lookup.
   (Students are warned at signup that admins can view passwords.)
   ============================================================ */

function findStudentByLogin(email, password) {
  if (!email || password == null) return null;
  const target = String(email).trim().toLowerCase();
  const students = getStudents();
  return students.find(s =>
    (s.studentEmail || '').trim().toLowerCase() === target &&
    s.password === password
  ) || null;
}

const STUDENT_SESSION_KEY = 'highergrade_student_session';

function getLoggedInStudent() {
  const id = sessionStorage.getItem(STUDENT_SESSION_KEY);
  if (!id) return null;
  return getStudents().find(s => s.id === id) || null;
}

function setLoggedInStudent(id) {
  if (id) sessionStorage.setItem(STUDENT_SESSION_KEY, id);
  else sessionStorage.removeItem(STUDENT_SESSION_KEY);
}

/* ============================================================
   POINT TRANSACTIONS LOG
   Every action that moves points is recorded here so admins
   can audit the camp economy.
   ============================================================ */

const TX_STORAGE_KEY = 'highergrade_transactions_v1';
const TX_MAX = 2000; // keep the most recent N entries

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
  try {
    const stored = localStorage.getItem(TX_STORAGE_KEY);
    if (stored) {
      const arr = JSON.parse(stored);
      if (Array.isArray(arr)) return arr;
    }
  } catch (e) { /* fall through */ }
  return [];
}

function saveTransactions(arr) {
  if (arr.length > TX_MAX) arr = arr.slice(-TX_MAX);
  try { localStorage.setItem(TX_STORAGE_KEY, JSON.stringify(arr)); } catch (e) { /* full — skip */ }
}

function clearTransactions() {
  localStorage.removeItem(TX_STORAGE_KEY);
}

/* ============================================================
   FULL DEV RESET — wipe all student-side data back to defaults.
   Keeps the staff records (those are content, not state).
   ============================================================ */
function devResetEverything() {
  // All student-facing storage keys
  localStorage.removeItem(STUDENT_STORAGE_KEY);    // students + roles + clicker + base-stats values
  localStorage.removeItem(CLASS_STORAGE_KEY);      // classes + class points + investment bank
  localStorage.removeItem(BASE_STAT_STORAGE_KEY);  // base-stat category definitions
  localStorage.removeItem(ROLES_STORAGE_KEY);      // role types
  localStorage.removeItem(TX_STORAGE_KEY);         // transactions log

  // Session keys (logged-in student, admin unlock, hunt counter)
  sessionStorage.removeItem(STUDENT_SESSION_KEY);
  sessionStorage.removeItem('highergrade_admin_unlocked');
  sessionStorage.removeItem('highergrade_signin_clicks');
}

function logTransaction(entry) {
  const arr = getTransactions();
  arr.push({
    id: 'tx-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
    at: Date.now(),
    ...entry,
  });
  saveTransactions(arr);
}

/* ============================================================
   STUDENT ROLES
   Admin defines role types; each student may hold zero or more.
   Roles are stored as an array of role-IDs on the student record.
   Only the student themselves (and admin) can see them.
   ============================================================ */

const ROLES_STORAGE_KEY = 'highergrade_roles_v1';
const MAZEWIZ_ROLE_ID = 'mazewiz';
const MAZEWIZ_PASSCODE = 'MazeWiz'; // student-facing claim code

const DEFAULT_ROLES = [
  {
    id: MAZEWIZ_ROLE_ID,
    name: 'Maze Wizard',
    icon: '🧙',
    color: '#8B5CF6',
    description: 'The first student from their class to find the hidden staff sign-in page. Grants permission to view classmates\' private stats and roles.',
    special: true,
  },
];

function getRoles() {
  try {
    const stored = localStorage.getItem(ROLES_STORAGE_KEY);
    if (stored) {
      const arr = JSON.parse(stored);
      if (Array.isArray(arr)) {
        // Guarantee the MazeWiz role always exists
        if (!arr.some(r => r.id === MAZEWIZ_ROLE_ID)) {
          arr.unshift(DEFAULT_ROLES[0]);
        }
        return arr;
      }
    }
  } catch (e) { /* fall through */ }
  return DEFAULT_ROLES.slice();
}

function saveRoles(arr) {
  localStorage.setItem(ROLES_STORAGE_KEY, JSON.stringify(arr));
}

function newRoleId() {
  return 'role-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
}

function addRole(role) {
  const arr = getRoles();
  if (arr.some(r => r.id === role.id || r.name.toLowerCase() === (role.name || '').toLowerCase())) {
    return { ok: false, error: 'A role with that ID or name already exists.' };
  }
  arr.push(role);
  saveRoles(arr);
  return { ok: true };
}

function updateRole(id, updates) {
  const arr = getRoles();
  const idx = arr.findIndex(r => r.id === id);
  if (idx < 0) return false;
  arr[idx] = { ...arr[idx], ...updates };
  saveRoles(arr);
  return true;
}

function deleteRole(id) {
  if (id === MAZEWIZ_ROLE_ID) return false; // protected
  saveRoles(getRoles().filter(r => r.id !== id));
  // Remove from every student too
  const students = getStudents();
  let changed = false;
  students.forEach(s => {
    if (Array.isArray(s.roles) && s.roles.includes(id)) {
      s.roles = s.roles.filter(x => x !== id);
      changed = true;
    }
  });
  if (changed) saveStudents(students);
  return true;
}

function assignRoleToStudent(studentId, roleId) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return false;
  s.roles = Array.isArray(s.roles) ? s.roles : [];
  if (!s.roles.includes(roleId)) s.roles.push(roleId);
  saveStudents(students);
  return true;
}

function removeRoleFromStudent(studentId, roleId) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s || !Array.isArray(s.roles)) return false;
  s.roles = s.roles.filter(x => x !== roleId);
  saveStudents(students);
  return true;
}

function studentHasRole(student, roleId) {
  return !!(student && Array.isArray(student.roles) && student.roles.includes(roleId));
}

/* MazeWiz: at most one winner per class. Returns the winning student
   or null if no-one from that class has claimed it yet. */
function getMazeWizWinnerForClass(classId) {
  if (!classId) return null;
  const students = getStudents();
  return students.find(s => s.classId === classId && studentHasRole(s, MAZEWIZ_ROLE_ID)) || null;
}

/* Try to claim the MazeWiz role for a student. Returns status object. */
function claimMazeWiz(studentId) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return { ok: false, error: 'Student not found.' };
  if (!s.classId) return { ok: false, error: 'You need to be assigned to a class first — ask an admin.' };
  if (studentHasRole(s, MAZEWIZ_ROLE_ID)) return { ok: false, error: 'You already hold the Maze Wizard title!' };
  const winner = getMazeWizWinnerForClass(s.classId);
  if (winner) {
    return { ok: false, error: `Too late — ${studentFullName(winner)} already claimed Maze Wizard for your class.`, winner };
  }
  s.roles = Array.isArray(s.roles) ? s.roles : [];
  s.roles.push(MAZEWIZ_ROLE_ID);
  saveStudents(students);
  return { ok: true };
}

/* ============================================================
   Student-facing actions
   Each returns { ok: bool, error?: string, data?: any }
   ============================================================ */

function _mutateStudent(id, fn) {
  const students = getStudents();
  const s = students.find(x => x.id === id);
  if (!s) return { ok: false, error: 'Student not found.' };
  s.stats = { ...defaultStats(), ...(s.stats || {}) };
  const result = fn(s, students);
  if (!result || result.ok !== false) saveStudents(students);
  return result || { ok: true };
}

function investInLuck(studentId) {
  const res = _mutateStudent(studentId, s => {
    const cur = s.stats.privatePoints || 0;
    if (cur === 0) return { ok: false, error: "You have 0 points! Ask an admin to award you some before you can upgrade your stats." };
    if (cur < LUCK_COST) return { ok: false, error: `You need ${LUCK_COST} points to invest. You only have ${cur} — keep earning!` };
    s.stats.privatePoints = cur - LUCK_COST;
    s.stats.luck = (s.stats.luck || 0) + 1;
    return { ok: true, data: { newLuck: s.stats.luck, remaining: s.stats.privatePoints } };
  });
  if (res && res.ok) {
    const s = getStudents().find(x => x.id === studentId);
    logTransaction({
      type: 'luck',
      scope: 'student',
      subjectId: studentId,
      subjectName: s ? studentFullName(s) : '',
      amount: -LUCK_COST,
      description: `Invested ${LUCK_COST} pts → luck now ${res.data.newLuck}`,
    });
  }
  return res;
}

function transferPoints(fromId, toId, amount) {
  amount = parseInt(amount, 10);
  if (!amount || amount <= 0) return { ok: false, error: 'Enter a positive amount to transfer.' };
  if (fromId === toId) return { ok: false, error: "You can't transfer points to yourself." };
  const students = getStudents();
  const from = students.find(x => x.id === fromId);
  const to   = students.find(x => x.id === toId);
  if (!from) return { ok: false, error: 'Your account was not found.' };
  if (!to)   return { ok: false, error: 'Recipient not found.' };

  from.stats = { ...defaultStats(), ...(from.stats || {}) };
  to.stats   = { ...defaultStats(), ...(to.stats   || {}) };

  const cur = from.stats.privatePoints || 0;
  if (cur === 0) return { ok: false, error: "You have 0 points! Ask an admin to award you some first." };
  if (cur < amount) return { ok: false, error: `You only have ${cur} points — you can't send ${amount}.` };

  const received = Math.floor(amount * TRANSFER_KEEP_RATIO);
  from.stats.privatePoints = cur - amount;
  from.stats.pointExchanges = (from.stats.pointExchanges || 0) + 1;
  to.stats.privatePoints   = (to.stats.privatePoints || 0) + received;
  to.stats.totalPointsEarned = (to.stats.totalPointsEarned || 0) + received;
  saveStudents(students);

  const fromName = studentFullName(from);
  const toName   = studentFullName(to);
  logTransaction({
    type: 'transfer_out',
    scope: 'student',
    subjectId: fromId,
    subjectName: fromName,
    relatedId: toId,
    relatedName: toName,
    amount: -amount,
    description: `Sent ${amount} pts to ${toName} · ${amount - received} pts lost in transfer`,
  });
  logTransaction({
    type: 'transfer_in',
    scope: 'student',
    subjectId: toId,
    subjectName: toName,
    relatedId: fromId,
    relatedName: fromName,
    amount: received,
    description: `Received ${received} pts from ${fromName} (${amount} sent, 50% kept)`,
  });

  return { ok: true, data: { sent: amount, received, lost: amount - received } };
}

function clickerTap(studentId) {
  const res = _mutateStudent(studentId, s => {
    s.stats.clickerClicks = (s.stats.clickerClicks || 0) + 1;
    let earned = 0;
    let spider = false;
    if (s.stats.clickerClicks % CLICKER_RATE === 0) {
      earned = 1;
      s.stats.privatePoints = (s.stats.privatePoints || 0) + 1;
      s.stats.totalPointsEarned = (s.stats.totalPointsEarned || 0) + 1;
      s.stats.clickerPointsEarned = (s.stats.clickerPointsEarned || 0) + 1;
      if (s.stats.clickerPointsEarned >= SPIDER_THRESHOLD && !s.stats.spiderShown) {
        spider = true;
        s.stats.spiderShown = true;
      }
    }
    return { ok: true, data: { clicks: s.stats.clickerClicks, earned, spider, clickerPointsEarned: s.stats.clickerPointsEarned } };
  });
  if (res && res.ok && res.data.earned > 0) {
    const s = getStudents().find(x => x.id === studentId);
    logTransaction({
      type: 'clicker',
      scope: 'student',
      subjectId: studentId,
      subjectName: s ? studentFullName(s) : '',
      amount: res.data.earned,
      description: `Earned ${res.data.earned} pt from clicker (${res.data.clicks} total clicks)`,
    });
  }
  return res;
}

/* Admin: overwrite arbitrary top-level fields on a student (name, email, password, etc.) */
function updateStudentProfile(id, updates) {
  const students = getStudents();
  const idx = students.findIndex(s => s.id === id);
  if (idx < 0) return false;
  const current = students[idx];
  const statsUpdate = updates.stats;
  const top = { ...updates };
  delete top.stats;
  students[idx] = { ...current, ...top };
  if (statsUpdate) students[idx].stats = { ...current.stats, ...statsUpdate };
  saveStudents(students);
  return true;
}

/* Admin penalty actions */
function applyPenalty(studentId, amount) {
  amount = parseInt(amount, 10) || 0;
  const res = _mutateStudent(studentId, s => {
    s.stats.privatePoints = Math.max(0, (s.stats.privatePoints || 0) - amount);
    return { ok: true, data: { amount, remaining: s.stats.privatePoints } };
  });
  if (res && res.ok) {
    const s = getStudents().find(x => x.id === studentId);
    logTransaction({
      type: 'penalty',
      scope: 'student',
      subjectId: studentId,
      subjectName: s ? studentFullName(s) : '',
      amount: -amount,
      description: `Admin penalty −${amount} pts`,
    });
  }
  return res;
}

function applyCursePenalty(studentId, amount) {
  amount = parseInt(amount, 10) || 0;
  const res = _mutateStudent(studentId, s => {
    s.stats.privatePoints = Math.max(0, (s.stats.privatePoints || 0) - amount);
    s.stats.badWords = (s.stats.badWords || 0) + 1;
    return { ok: true, data: { amount, remaining: s.stats.privatePoints, badWords: s.stats.badWords } };
  });
  if (res && res.ok) {
    const s = getStudents().find(x => x.id === studentId);
    logTransaction({
      type: 'curse',
      scope: 'student',
      subjectId: studentId,
      subjectName: s ? studentFullName(s) : '',
      amount: -amount,
      description: `Curse-word penalty −${amount} pts (bad-word count +1)`,
    });
  }
  return res;
}

/* ============================================================
   CLASSES
   Each class has: id, name, classPoints, classBank
   Students reference their class via student.classId.
   1 class point distributes 10 individual points to every member.
   ============================================================ */

const CLASS_STORAGE_KEY = 'highergrade_classes_v1';
const CLASS_POINT_TO_INDIVIDUAL = 10; // 1 class pt → 10 individual pts per member
const CLASS_BANK_DAILY_RATE = 0.05;   // bank grows 5% per day, compounded continuously

function getClasses() {
  try {
    const stored = localStorage.getItem(CLASS_STORAGE_KEY);
    if (stored) {
      const arr = JSON.parse(stored);
      if (Array.isArray(arr)) return arr;
    }
  } catch (e) { /* fall through */ }
  return [];
}

function saveClasses(arr) {
  localStorage.setItem(CLASS_STORAGE_KEY, JSON.stringify(arr));
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

function addClass(name) {
  name = String(name || '').trim();
  if (!name) return { ok: false, error: 'Class needs a name.' };
  const classes = getClasses();
  if (classes.some(c => c.name.toLowerCase() === name.toLowerCase())) {
    return { ok: false, error: `A class named "${name}" already exists.` };
  }
  const cls = {
    id: newClassId(),
    name,
    classPoints: 0,
    classBank: 0,
    createdAt: new Date().toISOString(),
  };
  classes.push(cls);
  saveClasses(classes);
  return { ok: true, data: cls };
}

function updateClass(id, updates) {
  const classes = getClasses();
  const idx = classes.findIndex(c => c.id === id);
  if (idx < 0) return false;
  classes[idx] = { ...classes[idx], ...updates };
  saveClasses(classes);
  // If renamed, refresh denormalized className on every member
  if (updates.name) {
    const students = getStudents();
    let changed = false;
    students.forEach(s => {
      if (s.classId === id) { s.className = updates.name; changed = true; }
    });
    if (changed) saveStudents(students);
  }
  return true;
}

function deleteClass(id) {
  saveClasses(getClasses().filter(c => c.id !== id));
  // Unassign any students who were in this class
  const students = getStudents();
  let changed = false;
  students.forEach(s => {
    if (s.classId === id) {
      s.classId = null;
      s.className = '';
      changed = true;
    }
  });
  if (changed) saveStudents(students);
}

function assignStudentToClass(studentId, classId) {
  const students = getStudents();
  const s = students.find(x => x.id === studentId);
  if (!s) return false;
  if (!classId) {
    s.classId = null;
    s.className = '';
  } else {
    const cls = getClassById(classId);
    s.classId = classId;
    s.className = cls ? cls.name : '';
  }
  saveStudents(students);
  return true;
}

/* Bank growth — earns CLASS_BANK_DAILY_RATE per day, compounded continuously.
   Call before reading or modifying cls.classBank to keep balance current. */
function refreshClassBank(cls) {
  if (!cls) return;
  const now = Date.now();
  if (!cls.bankLastUpdate) { cls.bankLastUpdate = now; return; }
  if (!cls.classBank || cls.classBank <= 0) { cls.bankLastUpdate = now; return; }
  const days = (now - cls.bankLastUpdate) / (1000 * 60 * 60 * 24);
  if (days <= 0) return;
  // Continuous compounding: balance grows by 0.05 per point per day on the running balance
  cls.classBank = cls.classBank * Math.pow(1 + CLASS_BANK_DAILY_RATE, days);
  cls.bankLastUpdate = now;
}

/* Convenience: returns classes with banks freshly compounded + saves if anything changed. */
function getClassesWithBankRefresh() {
  const classes = getClasses();
  let dirty = false;
  classes.forEach(cls => {
    const before = cls.classBank;
    refreshClassBank(cls);
    if (cls.classBank !== before) dirty = true;
  });
  if (dirty) saveClasses(classes);
  return classes;
}

/* Award (or revoke) class points into the unclaimed pool.
   These are NOT distributed yet — the class must claim or bank them. */
function awardClassPoints(classId, delta) {
  delta = parseInt(delta, 10) || 0;
  if (delta === 0) return { ok: false, error: 'Amount cannot be zero.' };
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  const newPool = Math.max(0, (cls.classPoints || 0) + delta);
  const actual = newPool - (cls.classPoints || 0);
  cls.classPoints = newPool;
  saveClasses(classes);
  logTransaction({
    type: 'class_award',
    scope: 'class',
    subjectId: classId,
    subjectName: cls.name,
    amount: actual,
    description: `${actual >= 0 ? '+' : ''}${actual} class pt${Math.abs(actual) === 1 ? '' : 's'} · unclaimed pool now ${newPool}`,
  });
  return { ok: true, data: { delta: actual, unclaimed: newPool } };
}

/* Claim N (or all) unclaimed class points → distribute ×10 to each member.
   Pass amount = null to claim all. */
function claimClassPoints(classId, amount) {
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  const available = cls.classPoints || 0;
  if (available <= 0) return { ok: false, error: 'No unclaimed class points to claim.' };
  amount = (amount == null) ? available : (parseInt(amount, 10) || 0);
  if (amount <= 0) return { ok: false, error: 'Amount must be positive.' };
  if (amount > available) amount = available;

  cls.classPoints = available - amount;
  saveClasses(classes);

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
  saveStudents(students);
  logTransaction({
    type: 'class_claim',
    scope: 'class',
    subjectId: classId,
    subjectName: cls.name,
    amount: -amount,
    description: `Claimed ${amount} class pt${amount === 1 ? '' : 's'} · distributed ${perMember * memberCount} pts across ${memberCount} member${memberCount === 1 ? '' : 's'}`,
  });
  return { ok: true, data: { claimed: amount, perMember, memberCount, totalDistributed: perMember * memberCount } };
}

/* Bank N (or all) unclaimed class points → moves to investment bank,
   where they accrue CLASS_BANK_DAILY_RATE per day. */
function bankClassPoints(classId, amount) {
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
  saveClasses(classes);
  logTransaction({
    type: 'class_bank_deposit',
    scope: 'class',
    subjectId: classId,
    subjectName: cls.name,
    amount,
    description: `Deposited ${amount} class pt${amount === 1 ? '' : 's'} to bank · new balance ${cls.classBank.toFixed(2)}`,
  });
  return { ok: true, data: { banked: amount, newBank: cls.classBank } };
}

/* Withdraw from bank → returns to the unclaimed class-points pool. */
function withdrawFromBank(classId, amount) {
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
  saveClasses(classes);
  logTransaction({
    type: 'class_bank_withdraw',
    scope: 'class',
    subjectId: classId,
    subjectName: cls.name,
    amount: -amount,
    description: `Withdrew ${amount} pts from bank to unclaimed pool`,
  });
  return { ok: true, data: { withdrawn: amount, newBank: cls.classBank, newUnclaimed: cls.classPoints } };
}

/* Direct admin override on the bank (rarely needed; kept for parity with old API) */
function adjustClassBank(classId, delta) {
  delta = parseInt(delta, 10) || 0;
  const classes = getClasses();
  const cls = classes.find(c => c.id === classId);
  if (!cls) return { ok: false, error: 'Class not found.' };
  refreshClassBank(cls);
  cls.classBank = Math.max(0, (cls.classBank || 0) + delta);
  cls.bankLastUpdate = Date.now();
  saveClasses(classes);
  logTransaction({
    type: 'class_bank_adjust',
    scope: 'class',
    subjectId: classId,
    subjectName: cls.name,
    amount: delta,
    description: `Admin bank adjustment ${delta >= 0 ? '+' : ''}${delta} · new balance ${cls.classBank.toFixed(2)}`,
  });
  return { ok: true, data: { newBalance: cls.classBank } };
}
