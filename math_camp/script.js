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

// ── Navbar: active link + scroll shadow + hamburger ──────────
(function () {
  const navbar    = document.querySelector('.navbar');
  const hamburger = document.querySelector('.hamburger');
  const navLinks  = document.querySelector('.nav-links');
  const page      = location.pathname.split('/').pop() || 'index.html';

  // Mark active nav link
  document.querySelectorAll('.nav-links a').forEach(a => {
    const href = a.getAttribute('href');
    if (href === page || (page === '' && href === 'index.html')) {
      a.classList.add('active');
    }
  });

  // Scroll shadow
  window.addEventListener('scroll', () => {
    navbar.classList.toggle('scrolled', window.scrollY > 10);
  }, { passive: true });

  // Hamburger toggle
  hamburger && hamburger.addEventListener('click', () => {
    navLinks.classList.toggle('open');
    const open = navLinks.classList.contains('open');
    hamburger.querySelectorAll('span')[0].style.transform = open ? 'rotate(45deg) translate(5px, 5px)' : '';
    hamburger.querySelectorAll('span')[1].style.opacity  = open ? '0' : '';
    hamburger.querySelectorAll('span')[2].style.transform = open ? 'rotate(-45deg) translate(5px, -5px)' : '';
  });

  // Close menu on link click (mobile)
  navLinks && navLinks.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => navLinks.classList.remove('open'));
  });
})();


// ── Login-aware navbar (Sign In → Profile when logged in) ────
(function () {
  if (typeof getLoggedInStudent !== 'function') return;
  const student = getLoggedInStudent();
  if (!student) return;

  const signInEl = document.querySelector('.nav-signin');
  if (signInEl) {
    const firstName = (student.firstName || 'Profile')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    signInEl.innerHTML = `👤 ${firstName}`;
    signInEl.href = 'student-portal.html';
    signInEl.title = 'Go to your student portal';
    signInEl.classList.add('logged-in');
  }

  // Hide the "Register Now" CTA from the top nav once logged in.
  // Footer "Register" link stays available for re-registration access.
  document.querySelectorAll('.nav-links .nav-cta').forEach(el => {
    const li = el.closest('li');
    (li || el).style.display = 'none';
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
(function () {
  const form = document.getElementById('reg-form');
  if (!form) return;

  const successEl = document.getElementById('form-success');

  function validate() {
    let ok = true;
    form.querySelectorAll('[required]').forEach(field => {
      const errEl = field.closest('.field')?.querySelector('.field-error');
      if (!field.value.trim()) {
        field.classList.add('error');
        if (errEl) errEl.classList.add('show');
        ok = false;
      } else {
        field.classList.remove('error');
        if (errEl) errEl.classList.remove('show');
      }
    });

    // Email format check
    form.querySelectorAll('input[type="email"]').forEach(f => {
      const errEl = f.closest('.field')?.querySelector('.field-error');
      if (f.value.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.value.trim())) {
        f.classList.add('error');
        if (errEl) { errEl.textContent = 'Please enter a valid email address.'; errEl.classList.add('show'); }
        ok = false;
      }
    });

    // Consent checkbox
    const consent = form.querySelector('#consent');
    if (consent && !consent.checked) {
      const errEl = consent.closest('.field')?.querySelector('.field-error');
      if (errEl) errEl.classList.add('show');
      ok = false;
    }

    // Camp password (min 4 chars)
    const pwd = form.querySelector('#camp-password');
    if (pwd) {
      const errEl = pwd.closest('.field')?.querySelector('.field-error');
      if (!pwd.value || pwd.value.length < 4) {
        pwd.classList.add('error');
        if (errEl) errEl.classList.add('show');
        ok = false;
      }
    }

    return ok;
  }

  // Live clear errors on input
  form.querySelectorAll('input, select, textarea').forEach(f => {
    f.addEventListener('input', () => {
      f.classList.remove('error');
      f.closest('.field')?.querySelector('.field-error')?.classList.remove('show');
    });
  });

  form.addEventListener('submit', e => {
    e.preventDefault();
    if (!validate()) {
      form.querySelector('.error')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    const btn = form.querySelector('[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Submitting…';

    // Persist student record (used by leaderboard + admin)
    if (typeof addStudent === 'function' && typeof newStudentId === 'function') {
      const fd = new FormData(form);
      const student = {
        id: newStudentId(),
        firstName:   (fd.get('first_name')   || '').toString().trim(),
        lastName:    (fd.get('last_name')    || '').toString().trim(),
        className:   '',
        dob:         (fd.get('dob')          || '').toString().trim(),
        school:      (fd.get('school')       || '').toString().trim(),
        studentEmail:(fd.get('student_email')|| '').toString().trim(),
        parentFirst: (fd.get('parent_first') || '').toString().trim(),
        parentLast:  (fd.get('parent_last')  || '').toString().trim(),
        parentEmail: (fd.get('parent_email') || '').toString().trim(),
        parentPhone: (fd.get('parent_phone') || '').toString().trim(),
        password:    (fd.get('camp_password') || '').toString(),
        baseStats:   {},
        registeredAt: new Date().toISOString(),
        stats: defaultStats(),
      };
      try {
        addStudent(student);
        // Auto-sign-in so portal works immediately
        if (typeof setLoggedInStudent === 'function') setLoggedInStudent(student.id);
      } catch (e) { /* localStorage may be full; non-fatal */ }
    }

    // Simulate a brief "send" delay then show success
    setTimeout(() => {
      form.style.display = 'none';
      successEl.classList.add('show');
      successEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 1000);
  });
})();

// ── Team rendering + profile modal (about page) ──────────────
(function () {
  const container = document.getElementById('team-categories');
  const modal = document.getElementById('team-modal');
  if (!container || !modal || typeof STAFF_CATEGORIES === 'undefined') return;

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
        transcriptDownloadEl.textContent = `📄 Download ${s.transcriptFile.name || 'transcript file'}`;
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

  const target = new Date('2026-07-20T09:00:00');

  function tick() {
    const now  = new Date();
    const diff = target - now;
    if (diff <= 0) { el.style.display = 'none'; return; }
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000)  / 60000);
    const s = Math.floor((diff % 60000)    / 1000);
    el.innerHTML =
      `<span>${d}<small>d</small></span><span>${h}<small>h</small></span><span>${m}<small>m</small></span><span>${s}<small>s</small></span>`;
  }
  tick();
  setInterval(tick, 1000);
})();
