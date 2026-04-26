# One-shot migration: existing localStorage → server SQLite

The site used to store everything in browser `localStorage`, so any
real student data you collected during testing lives only in the
browser you registered them from. Use this guide once, on each
browser that has data worth keeping.

## Step 1 — Sign in as admin on the production site

Open <https://highergradetutoring.ca/admin.html> and unlock with the
admin passcode. This sets the admin session cookie that the import
endpoint requires.

## Step 2 — Open DevTools on the same browser tab

`F12` (or right-click → Inspect) → **Console** tab.

## Step 3 — Paste this snippet and press Enter

```js
(async () => {
  function pull(k) {
    try { return JSON.parse(localStorage.getItem(k) || 'null'); }
    catch (_) { return null; }
  }
  const payload = {
    students:     pull('highergrade_students_v1')   || undefined,
    classes:      pull('highergrade_classes_v1')    || undefined,
    roles:        pull('highergrade_roles_v1')      || undefined,
    baseStats:    pull('highergrade_base_stats_v1') || undefined,
    transactions: pull('highergrade_transactions_v1') || undefined,
    staff:        pull('highergrade_staff_v1')      || undefined,
  };
  Object.keys(payload).forEach(k => payload[k] === undefined && delete payload[k]);
  if (Object.keys(payload).length === 0) {
    console.log('Nothing to migrate — no localStorage keys found.');
    return;
  }
  const res = await fetch('/api/admin/import', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const json = await res.json();
  console.log('Import response:', json);
  if (json.ok) {
    alert('Migration complete:\n' + JSON.stringify(json.imported, null, 2));
  } else {
    alert('Migration failed: ' + (json.error || 'unknown error'));
  }
})();
```

The snippet:
1. Reads each known `highergrade_*_v1` key from this browser's
   localStorage.
2. POSTs the bundle to `/api/admin/import`, which the Flask
   backend uses to **replace** the corresponding tables in the
   server-side SQLite DB.
3. Shows you the row counts that were imported.

## Step 4 — Verify on the admin pages

Refresh <https://highergradetutoring.ca/admin-students.html> and
confirm the imported students show up. The server is now the
source of truth; localStorage is no longer read by the site.

## Optional — Clean up old localStorage

You can leave the keys in place (the site no longer reads them), or
clear them to free up the space:

```js
['highergrade_students_v1','highergrade_classes_v1','highergrade_roles_v1',
 'highergrade_base_stats_v1','highergrade_transactions_v1',
 'highergrade_staff_v1','highergrade_admin_unlocked',
 'highergrade_student_session','highergrade_signin_clicks']
  .forEach(k => localStorage.removeItem(k));
```
