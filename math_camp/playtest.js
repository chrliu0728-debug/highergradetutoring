/* ============================================================
   PLAYTEST MODE — admin's "see it as a camper does" view.
   ----------------------------------------------------------------
   Started from the admin panel (POST /api/auth/playtest/start),
   which mints a real student session for the reserved "HGT TEST"
   camper while keeping the admin session alive server-side.

   This file is the student-side half: it paints the banner and
   listens for Esc, which hands the cookie back to the admin
   session and returns to /admin/admin.html.

   Include it on every student-facing page — it costs nothing and
   does nothing at all when the tab isn't in playtest.
   ============================================================ */
(function () {
  'use strict';

  var FLAG = 'hg_playtest';
  var RETURN_TO = '/admin/admin.html';
  var active = false;
  var leaving = false;

  function flagged() {
    try { return sessionStorage.getItem(FLAG) === '1'; } catch (_) { return false; }
  }

  function ensureStyles() {
    if (document.getElementById('hg-playtest-style')) return;
    var css = document.createElement('style');
    css.id = 'hg-playtest-style';
    css.textContent = [
      '#hg-playtest-bar{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;',
      'display:flex;align-items:center;justify-content:center;gap:14px;flex-wrap:wrap;',
      'padding:8px 16px;font:600 .82rem/1.3 system-ui,-apple-system,"Segoe UI",sans-serif;',
      'color:#3B0764;background:linear-gradient(90deg,#FDE68A,#FCA5A5);',
      'box-shadow:0 -2px 14px rgba(0,0,0,.18);letter-spacing:.01em;}',
      '#hg-playtest-bar .hg-pt-dot{width:8px;height:8px;border-radius:50%;background:#DC2626;',
      'display:inline-block;margin-right:7px;animation:hg-pt-pulse 1.6s ease-in-out infinite;}',
      '#hg-playtest-bar kbd{font:700 .78rem/1 ui-monospace,Menlo,monospace;background:#fff;',
      'border:1px solid rgba(0,0,0,.18);border-bottom-width:2px;border-radius:5px;padding:3px 7px;}',
      '#hg-playtest-exit{background:#3B0764;color:#fff;border:none;border-radius:999px;',
      'padding:6px 14px;font:700 .78rem/1 system-ui,sans-serif;cursor:pointer;}',
      '#hg-playtest-exit:hover{background:#581C87;}',
      // Keep the bar from sitting on top of the last line of the page.
      'body.hg-playtest-on{padding-bottom:52px;}',
      '@keyframes hg-pt-pulse{0%,100%{opacity:1}50%{opacity:.25}}',
      '@media print{#hg-playtest-bar{display:none}}',
    ].join('');
    document.head.appendChild(css);
  }

  function paint() {
    if (document.getElementById('hg-playtest-bar')) return;
    if (!document.body) {
      document.addEventListener('DOMContentLoaded', paint, { once: true });
      return;
    }
    ensureStyles();
    var bar = document.createElement('div');
    bar.id = 'hg-playtest-bar';
    bar.setAttribute('role', 'status');
    bar.innerHTML =
      '<span><span class="hg-pt-dot"></span>PLAYTEST — you are seeing the site as <strong>HGT TEST</strong></span>' +
      '<span>Press <kbd>Esc</kbd> to return to admin</span>' +
      '<button type="button" id="hg-playtest-exit">Exit playtest</button>';
    document.body.appendChild(bar);
    document.body.classList.add('hg-playtest-on');
    document.getElementById('hg-playtest-exit').addEventListener('click', exit);
  }

  function unpaint() {
    var bar = document.getElementById('hg-playtest-bar');
    if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
    if (document.body) document.body.classList.remove('hg-playtest-on');
  }

  async function exit() {
    if (!active || leaving) return;
    leaving = true;
    var to = RETURN_TO;
    try {
      var res = await fetch('/api/auth/playtest/stop', {
        method: 'POST', credentials: 'same-origin',
      });
      var data = null;
      try { data = await res.json(); } catch (_) { /* no body */ }
      if (data && data.returnTo) to = data.returnTo;
      // A 409 means the admin session behind this playtest expired. There's
      // nothing to hand the cookie back to, so we still head for the admin
      // page — it'll show the passcode gate.
    } catch (_) { /* offline — go anyway */ }
    try { sessionStorage.removeItem(FLAG); } catch (_) {}
    window.location.href = to;
  }

  /* ── Admin side: entering playtest ──────────────────────────── */
  async function start() {
    var res = await fetch('/api/auth/playtest/start', {
      method: 'POST', credentials: 'same-origin',
    });
    var data = null;
    try { data = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok || !data || !data.ok) {
      throw new Error((data && data.error) || 'Could not start playtest mode.');
    }
    try { sessionStorage.setItem(FLAG, '1'); } catch (_) {}
    window.location.href = '/student-portal/student-portal.html';
  }

  // Any admin page can drop in a `data-playtest-start` element and get the
  // whole flow for free — no per-page wiring.
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest && e.target.closest('[data-playtest-start]');
    if (!el) return;
    e.preventDefault();
    if (el.dataset.busy === '1') return;
    el.dataset.busy = '1';
    start().catch(function (err) {
      el.dataset.busy = '';
      alert(err.message || 'Could not start playtest mode.');
    });
  });

  function activate() {
    if (active) return;
    active = true;
    paint();
  }

  function deactivate() {
    active = false;
    unpaint();
  }

  // Pages across the site close their modals with Esc. If one is open,
  // that's what the press means — leave it alone and let the page handle
  // it. Only a "nothing is open" Esc exits playtest.
  function modalOpen() {
    try {
      return !!document.querySelector('dialog[open], .modal.open, .overlay.open, [class*="modal"].open');
    } catch (_) { return false; }
  }

  document.addEventListener('keydown', function (e) {
    if (!active) return;
    if (e.key !== 'Escape' && e.key !== 'Esc') return;
    if (modalOpen()) return;
    e.preventDefault();
    e.stopPropagation();
    exit();
  }, true);   // capture, so a game's own Esc handler can't swallow it

  // Called by students-data.js once /auth/me comes back, which is the
  // authoritative answer. The sessionStorage flag below is only there so
  // the banner appears instantly instead of after the bootstrap fetch.
  window.HGPlaytest = {
    sync: function (on) { on ? activate() : deactivate(); },
    start: start,
    exit: exit,
    get active() { return active; },
  };

  if (flagged()) activate();
})();
