/* ============================================================
   STAFF DATA — HigherGrade Tutoring  (server-backed)
   Reads from window.HG.cache.staff (populated by students-data.js
   bootstrap). Mutations PUT to /api/staff.
   ============================================================ */

const STAFF_CATEGORIES = [
  { id: 'organizers',          label: 'Organizers',          desc: 'The students who conceived, designed, and built this camp from scratch.' },
  { id: 'teaching_staff',      label: 'Teaching Staff',      desc: 'Senior HDSB students who lead the lessons and write the exams.' },
  { id: 'teaching_assistants', label: 'Teaching Assistants', desc: 'TAs who sit alongside students during practice periods and help debug tough problems.' },
  { id: 'general_staff',       label: 'General Staff',       desc: 'Logistics, tech, and the behind-the-scenes crew keeping the camp running.' },
  { id: 'supervisors',         label: 'Supervisors',         desc: 'Faculty advisors providing oversight, safety, and mentorship.' },
  { id: 'partners',            label: 'Partners',            desc: 'Organizations and individuals who made this program possible.' },
];

function getStaff() {
  const c = (window.HG && window.HG.cache && window.HG.cache.staff) || [];
  return c.slice();
}

async function saveStaff(arr) {
  if (window.HG && window.HG.cache) window.HG.cache.staff = arr;
  const init = {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ staff: arr }),
  };
  const res = await fetch('/api/staff', init);
  if (!res.ok) throw new Error('Failed to save staff (' + res.status + ')');
  return res.json();
}

async function resetStaff() {
  // Server-side reset: empty out then re-seed by re-fetching defaults.
  await saveStaff([]);
  const r = await fetch('/api/staff', { credentials: 'same-origin' });
  const j = await r.json();
  if (window.HG && window.HG.cache) window.HG.cache.staff = j.data || [];
}
