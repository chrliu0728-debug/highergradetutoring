/* ============================================================
   MATH CAMP 2026 — Shared JavaScript
   ============================================================ */

// ── Session housekeeping ──
// script.js is only loaded on public (non-admin) pages, so visiting any
// such page automatically logs the admin out of their passcode session.
// (Admin pages re-check the session on load and re-show the gate.)
sessionStorage.removeItem('highergrade_admin_unlocked');

// Reset the hidden staff sign-in click counter on home page only.
// The student-portal page persists "Sign in" click attempts in
// sessionStorage so users can't just refresh to get back to 0.
// Visiting the home page is the ONLY way to reset the counter.
(function () {
  const path = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  if (path === 'index.html' || path === '' || path === '/') {
    sessionStorage.removeItem('highergrade_signin_clicks');
  }
})();

// ── Sidenav: active link + mobile toggle ──────────────────────
(function () {
  const sidenav = document.getElementById('sidenav');
  const toggle  = document.getElementById('sidenav-toggle');
  const overlay = document.getElementById('sidenav-overlay');
  if (!sidenav) return;

  // Mark active nav link
  const norm = p => p.replace(/\/index\.html$/, '/') || '/';
  const here = norm(location.pathname);
  document.querySelectorAll('.sidenav-links a').forEach(a => {
    const raw = a.getAttribute('href');
    if (!raw) return;
    const ahref = norm(new URL(raw, location.href).pathname);
    if (ahref === here) a.classList.add('active');
  });

  // Mobile open/close
  function openNav() {
    sidenav.classList.add('open');
    if (overlay) overlay.classList.add('show');
    document.body.style.overflow = 'hidden';
  }
  function closeNav() {
    sidenav.classList.remove('open');
    if (overlay) overlay.classList.remove('show');
    document.body.style.overflow = '';
  }
  toggle  && toggle.addEventListener('click', openNav);
  overlay && overlay.addEventListener('click', closeNav);
  document.querySelectorAll('.sidenav-links a').forEach(a =>
    a.addEventListener('click', closeNav)
  );
})();


// ── Login-aware navbar (Sign In → Profile when logged in) ────
(async function () {
  if (typeof getLoggedInStudent !== 'function') return;
  if (window.dataReady) { try { await window.dataReady; } catch (_) {} }
  const student = getLoggedInStudent();
  if (!student) return;

  const signInEl = document.querySelector('.nav-signin');
  if (signInEl) {
    const firstName = (student.firstName || 'Profile')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const userIcon = window.Icons ? Icons.svg('user') : '';
    signInEl.innerHTML = `${userIcon}<span>${firstName}</span>`;
    signInEl.href = '/student-portal/student-portal.html';
    signInEl.title = 'Go to your student portal';
    signInEl.classList.add('logged-in');
  }

  // Hide the "Register Now" CTA from the sidenav once logged in.
  // Footer "Register" link stays available for re-registration access.
  document.querySelectorAll('.sidenav-cta').forEach(el => {
    el.style.display = 'none';
  });
})();

// ── Back-to-top button ────────────────────────────────────────
(function () {
  const btn = document.createElement('button');
  btn.className = 'back-to-top';
  btn.setAttribute('aria-label', 'Back to top');
  btn.innerHTML = '↑';
  document.body.appendChild(btn);

  function update() {
    btn.classList.toggle('visible', window.scrollY > 400);
  }

  window.addEventListener('scroll', update, { passive: true });
  btn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  update();
})();

// ── Scroll-reveal animation ───────────────────────────────────
(function () {
  const observer = new IntersectionObserver(
    entries => entries.forEach(e => {
      if (e.isIntersecting) {
        e.target.style.opacity = '1';
        e.target.style.transform = 'translateY(0)';
        observer.unobserve(e.target);
      }
    }),
    { threshold: 0.08 }
  );

  document.querySelectorAll('.card, .timeline-item, .support-tier, .faq-item, .reveal').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(24px)';
    el.style.transition = 'opacity .5s ease, transform .5s ease';
    observer.observe(el);
  });
})();

// ── FAQ accordion ─────────────────────────────────────────────
document.querySelectorAll('.faq-q').forEach(btn => {
  btn.addEventListener('click', () => {
    const item = btn.closest('.faq-item');
    const isOpen = item.classList.contains('open');
    document.querySelectorAll('.faq-item.open').forEach(i => i.classList.remove('open'));
    if (!isOpen) item.classList.add('open');
  });
});

// ── Registration form ─────────────────────────────────────────
// Registration is handled entirely by the inline script in
// register.html (multi-step form → POST /api/camp/register). The old
// single-step handler that used to live here was removed — it bound a
// second submit listener to #reg-form and re-saved via a stale path,
// so every submit ran twice. Nothing to do here now.

// ── Team rendering + profile modal (about page) ──────────────
(async function () {
  const container = document.getElementById('team-categories');
  const modal = document.getElementById('team-modal');
  if (!container || !modal || typeof STAFF_CATEGORIES === 'undefined') return;
  if (window.dataReady) { try { await window.dataReady; } catch (_) {} }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function cardHtml(s) {
    return `
      <div class="team-card" data-id="${esc(s.id)}">
        <div class="team-avatar"><img src="${esc(s.image)}" alt="${esc(s.name)}" onerror="this.style.opacity='0'" /></div>
        <div class="team-name">${esc(s.name)}</div>
        <div class="team-role">${esc(s.role)}</div>
        <blockquote class="team-card-quote">"${esc(s.quote || '')}"</blockquote>
      </div>
    `;
  }

  function renderAll() {
    const staff = (typeof getStaff === 'function') ? getStaff() : [];
    container.innerHTML = '';
    STAFF_CATEGORIES.forEach(cat => {
      const members = staff.filter(s => s.category === cat.id);
      if (members.length === 0) return;
      const section = document.createElement('div');
      section.className = 'team-category';
      section.innerHTML = `
        <div class="team-category-head">
          <div class="section-label">${esc(cat.label)}</div>
          <p class="team-category-desc">${esc(cat.desc)}</p>
        </div>
        <div class="grid-4">${members.map(cardHtml).join('')}</div>
      `;
      container.appendChild(section);
    });
    attachCardHandlers();
  }

  // ── Modal handling ──
  const imgEl      = document.getElementById('team-modal-img');
  const nameEl     = document.getElementById('team-modal-name');
  const roleEl     = document.getElementById('team-modal-role');
  const quoteEl    = document.getElementById('team-modal-quote');
  const ageEl      = document.getElementById('team-modal-age');
  const schoolEl   = document.getElementById('team-modal-school');
  const genderEl   = document.getElementById('team-modal-gender');
  const pronounsEl = document.getElementById('team-modal-pronouns');
  const interestsEl= document.getElementById('team-modal-interests');
  const bioEl      = document.getElementById('team-modal-bio');
  const transcriptEl      = document.getElementById('team-modal-transcript');
  const transcriptSection = document.getElementById('team-modal-transcript-section');
  const transcriptDownloadEl = document.getElementById('team-modal-transcript-download');
  const closeBtn  = document.getElementById('team-modal-close');
  const backdrop  = modal.querySelector('.team-modal-backdrop');

  function openModal(s) {
    if (!s) return;
    imgEl.src = s.image || '';
    imgEl.alt = s.name || '';
    nameEl.textContent     = s.name     || '';
    roleEl.textContent     = s.role     || '';
    quoteEl.textContent    = s.quote ? `"${s.quote}"` : '';
    ageEl.textContent      = s.age      || '—';
    schoolEl.textContent   = s.school   || '—';
    genderEl.textContent   = s.gender   || '—';
    pronounsEl.textContent = s.pronouns || '—';
    interestsEl.textContent= s.interests|| '—';
    bioEl.textContent      = s.bio      || '—';

    const hasTranscriptText = s.transcript && s.transcript.trim();
    const hasTranscriptFile = s.transcriptFile && s.transcriptFile.data;

    if (hasTranscriptText) {
      transcriptEl.textContent = s.transcript;
      transcriptEl.style.display = '';
    } else {
      transcriptEl.textContent = '';
      transcriptEl.style.display = 'none';
    }

    if (transcriptDownloadEl) {
      if (hasTranscriptFile) {
        transcriptDownloadEl.href = s.transcriptFile.data;
        transcriptDownloadEl.download = s.transcriptFile.name || 'transcript';
        transcriptDownloadEl.innerHTML = `${window.Icons ? Icons.svg('file') : ''}<span>Download ${(s.transcriptFile.name || 'transcript file').replace(/[&<>]/g, '')}</span>`;
        transcriptDownloadEl.style.display = '';
      } else {
        transcriptDownloadEl.style.display = 'none';
      }
    }

    if (hasTranscriptText || hasTranscriptFile) {
      transcriptSection.style.display = '';
    } else {
      transcriptSection.style.display = 'none';
    }

    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  }

  function closeModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  function attachCardHandlers() {
    const staff = (typeof getStaff === 'function') ? getStaff() : [];
    container.querySelectorAll('.team-card').forEach(card => {
      card.setAttribute('tabindex', '0');
      card.setAttribute('role', 'button');
      card.addEventListener('click', () => {
        const id = card.dataset.id;
        openModal(staff.find(s => s.id === id));
      });
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          const id = card.dataset.id;
          openModal(staff.find(s => s.id === id));
        }
      });
    });
  }

  backdrop.addEventListener('click', closeModal);
  closeBtn.addEventListener('click', closeModal);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
  });

  renderAll();
})();

// ── Countdown timer (home page) ───────────────────────────────
(function () {
  const el = document.getElementById('countdown');
  if (!el) return;

  // Camp runs Aug 4 → Aug 15, 2026. Count down to the 8:45 AM Day 1 start,
  // switch to an "in session" badge during camp, then disappear after.
  const start = new Date('2026-08-04T08:45:00');
  const end   = new Date('2026-08-15T16:45:00');

  function tick() {
    const now = new Date();
    if (now >= end) { el.style.display = 'none'; return; }
    if (now >= start) {
      el.innerHTML = `<div class="cd-live">${window.Icons ? Icons.svg('sparkles') : ''} Camp is in session!</div>`;
      return;
    }
    const diff = start - now;
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000)  / 60000);
    const s = Math.floor((diff % 60000)    / 1000);
    el.innerHTML =
      `<div class="cd-label">Camp starts in</div>` +
      `<div class="cd-boxes">` +
        `<span>${d}<small>Days</small></span>` +
        `<span>${h}<small>Hours</small></span>` +
        `<span>${m}<small>Mins</small></span>` +
        `<span>${s}<small>Secs</small></span>` +
      `</div>`;
  }
  tick();
  setInterval(tick, 1000);
})();
