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
// Fades elements in on scroll. Deliberately opacity-only: these cards
// have their own hover transforms in CSS (lift, diagonal stagger), and
// an inline `transform` set here would permanently win the cascade
// over any stylesheet rule, killing those hover effects for good.
(function () {
  const observer = new IntersectionObserver(
    entries => entries.forEach(e => {
      if (e.isIntersecting) {
        e.target.style.opacity = '1';
        observer.unobserve(e.target);
      }
    }),
    { threshold: 0.08 }
  );

  const vh = window.innerHeight || document.documentElement.clientHeight;
  document.querySelectorAll('.card, .timeline-item, .support-tier, .faq-item, .reveal').forEach(el => {
    // Elements already on-screen at load must NOT be hidden here: this script
    // runs a beat after first paint on a cold load, so blanking already-painted
    // content makes it blink out and fade back in (looks broken on first load,
    // fine on cached nav). Only set up the reveal for below-the-fold elements.
    if (el.getBoundingClientRect().top < vh) return;
    el.style.opacity = '0';
    el.style.transition = 'opacity .5s ease';
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
        <div class="team-photo"><img src="${esc(s.image)}" alt="${esc(s.name)}" onerror="this.style.opacity='0'" /></div>
        <div class="team-label">
          <div class="team-name">${esc(s.name)}</div>
          <div class="team-role">${esc(s.role)}</div>
        </div>
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
        <div class="team-row">${members.map(cardHtml).join('')}</div>
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

  // The staff grid renders asynchronously (once the staff data has loaded),
  // which grows the page height above every section below it. If we arrived
  // via a deep link with a URL hash — e.g. /about/about.html#online from the
  // register form's "How online works" link — the browser already scrolled to
  // where that target sat BEFORE the grid rendered, so it lands short of the
  // real block. Re-scroll to the hash target now that the grid has expanded.
  if (location.hash.length > 1) {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target) {
      // rAF so the browser has flushed layout from the innerHTML writes above.
      requestAnimationFrame(() => target.scrollIntoView());
    }
  }
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

// ── Title sponsors in the sidebar nav (under Contact), site-wide ──────
// Injects the title-sponsor slots into the vertical sidenav (below the nav
// links, above Sign In / Register) and a themed detail modal into the body,
// on every page that loads this script. Asset paths are absolute so they
// resolve the same from pages nested in sub-folders (/about/, /support/, …).
(function () {
  const SPONSORS = [
    {
      id: 'staples',
      name: 'Staples',
      tagline: 'Work. Learn. Grow.',
      brandColor: '#CC0000',
      logo: '/Staples_Canada_logo_2018.png',
      description:
        'Staples Canada is one of the country’s largest retailers for office, '
        + 'technology, and school essentials, with locations from coast to coast. '
        + 'Reimagined as “The Working and Learning Company,” Staples has grown '
        + 'beyond supplies into a community hub — its stores feature coworking '
        + 'spaces, classrooms, and event areas built to help people work, learn, and '
        + 'grow. Staples Canada is proud to invest in students, educators, and local '
        + 'communities, championing programs that make learning more accessible for the '
        + 'next generation.',
      location: {
        label: 'Oakville, Ontario',
        url: 'https://www.google.com/maps/dir//Staples,+320+North+Service+Rd+W,+Oakville,+ON+L6M+0H4/data=!4m6!4m5!1m1!4e2!1m2!1m1!1s0x882b5d067a689397:0x1c9e4d6912c2bda7?sa=X&ved=1t:57443&ictx=111',
      },
      images: [
        '/Staples_Oakville_storefront.jpg',
        '/3.jpg',
        '/1.jpg',
        '/download.webp',
      ],
      people: [
        { name: 'Terry Carson',   role: 'Vice Chair, Staples Canada — Oakville', photo: '' },
        { name: 'Elsa Abikhalil', role: 'Vice Chair, Staples Canada — Oakville', photo: '' },
      ],
    },
    {
      id: 'gametime',
      name: 'Game Time Collectibles',
      tagline: 'Home of the Goats',
      brandColor: '#EF9F00',
      logo: '/gametime-logo.jpg',
      description:
        'A one-stop shop for gamers, by gamers — trading cards (Pokémon, '
        + 'Magic, One Piece), Gunpla and model kits, figures, video games, and '
        + 'tabletop favourites, plus the tournaments and trade nights that turn '
        + 'the shop into a community hangout. Proud to help local students get '
        + 'a strong start.',
      location: {
        label: 'Game Time Collectibles — Mississauga',
        url: 'https://www.google.com/maps/place/Game+Time+Collectibles/@43.51261,-79.6414099,17z/data=!3m1!4b1!4m6!3m5!1s0x882b452c1180140b:0x839ae84e50cf34be!8m2!3d43.51261!4d-79.6414099!16s%2Fg%2F11h7cqqv2m',
      },
      images: [
        '/gametime-store.jpg',
        '/gametime-pokemon.jpg',
        '/gametime-gundam.jpg',
        '/gametime-mtg.jpg',
      ],
    },
    {
      id: 'maplestaple',
      name: 'The Maple Staple',
      tagline: 'Toronto indie bookstore',
      brandColor: '#CB4623',
      logo: '/maplestaple-logo.png',
      description:
        'A Toronto-based independent bookstore and community hub championing '
        + 'indie authors and a love of literature. Proud to invest in students’ '
        + 'academic confidence and problem-solving skills — building the '
        + 'creative leaders of tomorrow.',
      location: {
        label: 'The Maple Staple — Toronto',
        url: 'https://maps.app.goo.gl/vStM7ALktDi61aNE9',
      },
      images: [
        '/maplestaple-store.jpg',
        '/maplestaple-building.jpg',
        '/maplestaple-covers.jpg',
      ],
    },
    // Add up to 1 more sponsor object here when you have them.
  ];

  if (!SPONSORS.length) return;
  const nav = document.querySelector('.sidenav .sidenav-inner');
  if (!nav || document.getElementById('title-sponsors')) return;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  const initials = n => (n || '?').trim().split(/\s+/).map(w => w[0] || '').slice(0,2).join('').toUpperCase();

  // Slots block → sidebar, placed under the nav links (i.e. under Contact).
  const wrap = document.createElement('div');
  wrap.className = 'sidenav-sponsors';
  wrap.innerHTML = '<div class="sidenav-sponsors-label">Title Sponsors</div>'
                 + '<div class="title-sponsors" id="title-sponsors"></div>';
  const bottom = nav.querySelector('.sidenav-bottom');
  if (bottom) nav.insertBefore(wrap, bottom); else nav.appendChild(wrap);

  // Detail modal → body (once).
  let modal = document.getElementById('sponsor-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.className = 'sponsor-modal';
    modal.id = 'sponsor-modal';
    modal.setAttribute('aria-hidden', 'true');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', 'Sponsor details');
    modal.innerHTML =
      '<div class="sponsor-modal-backdrop" data-sponsor-close></div>' +
      '<div class="sponsor-modal-card" role="document">' +
        '<button class="sponsor-modal-close" data-sponsor-close aria-label="Close">×</button>' +
        '<div class="sponsor-modal-head">' +
          '<img class="sponsor-modal-logo" id="sm-logo" alt="" />' +
          '<div><div class="sponsor-modal-eyebrow">Title Sponsor</div>' +
          '<h2 class="sponsor-modal-name" id="sm-name"></h2>' +
          '<p class="sponsor-modal-tagline" id="sm-tagline"></p></div>' +
        '</div>' +
        '<div id="sm-carousel">' +
          '<div class="sponsor-carousel">' +
            '<button class="sponsor-carousel-arrow prev" id="sm-prev" aria-label="Previous image">‹</button>' +
            '<div class="sponsor-carousel-viewport"><div class="sponsor-carousel-track" id="sm-track"></div></div>' +
            '<button class="sponsor-carousel-arrow next" id="sm-next" aria-label="Next image">›</button>' +
          '</div>' +
          '<div class="sponsor-carousel-dots" id="sm-dots"></div>' +
        '</div>' +
        '<div class="sponsor-location" id="sm-location"></div>' +
        '<div class="sponsor-modal-desc" id="sm-desc"></div>' +
        '<div class="sponsor-people-label" id="sm-people-label">In partnership with</div>' +
        '<div class="sponsor-people" id="sm-people"></div>' +
      '</div>';
    document.body.appendChild(modal);
  }

  const slotsWrap = document.getElementById('title-sponsors');
  const elCard  = modal.querySelector('.sponsor-modal-card');
  const elLogo  = document.getElementById('sm-logo');
  const elName  = document.getElementById('sm-name');
  const elTag   = document.getElementById('sm-tagline');
  const elTrack = document.getElementById('sm-track');
  const elDots  = document.getElementById('sm-dots');
  const elLoc   = document.getElementById('sm-location');
  const elDesc  = document.getElementById('sm-desc');
  const elPeople= document.getElementById('sm-people');
  const elCarousel = document.getElementById('sm-carousel');
  const elPeopleLabel = document.getElementById('sm-people-label');
  const elPrev  = document.getElementById('sm-prev');
  const elNext  = document.getElementById('sm-next');

  let slide = 0;

  slotsWrap.innerHTML = SPONSORS.map(s => `
    <button class="sponsor-slot" data-id="${esc(s.id)}" title="${esc(s.name)} — click for details">
      ${s.logo
        ? `<img src="${esc(s.logo)}" alt="${esc(s.name)}" class="sponsor-slot-img" />`
        : `<span class="sponsor-slot-wordmark" style="color:${esc(s.brandColor || 'var(--text)')}">${esc(s.name)}</span>`}
    </button>`).join('');

  function renderCarousel(s) {
    const imgs = (s.images || []).slice(0, 4);
    if (!imgs.length) imgs.push('');
    elTrack.innerHTML = imgs.map((src, i) =>
      `<div class="sponsor-slide">${src
        ? `<img src="${esc(src)}" alt="${esc(s.name)} photo ${i+1}" />`
        : `<div class="sponsor-slide-placeholder">Image ${i+1}<br><small>add a photo</small></div>`}</div>`).join('');
    elDots.innerHTML = imgs.map((_, i) =>
      `<button class="sponsor-dot" data-i="${i}" aria-label="Go to image ${i+1}"></button>`).join('');
    slide = 0; update();
  }
  function update() {
    elTrack.style.transform = `translateX(-${slide * 100}%)`;
    [...elDots.children].forEach((d, i) => d.classList.toggle('active', i === slide));
  }
  function go(n) { const c = elTrack.children.length || 1; slide = (n + c) % c; update(); }
  elPrev.addEventListener('click', () => go(slide - 1));
  elNext.addEventListener('click', () => go(slide + 1));
  elDots.addEventListener('click', e => { const d = e.target.closest('.sponsor-dot'); if (d) go(+d.dataset.i); });

  function open(s) {
    elCard.style.setProperty('--sponsor-accent', s.brandColor || 'var(--blush)');
    if (s.logo) {
      elLogo.src = s.logo; elLogo.style.display = '';
      elName.style.display = 'none'; elTag.style.display = 'none';
    } else {
      elLogo.style.display = 'none'; elName.style.display = ''; elTag.style.display = '';
      elName.style.color = s.brandColor || '';
    }
    elName.textContent = s.name || '';
    elTag.textContent  = s.tagline || '';
    elDesc.textContent = s.description || '';
    if (s.location && s.location.label) {
      elLoc.innerHTML = `<a href="${esc(s.location.url)}" target="_blank" rel="noopener">${window.Icons ? Icons.svg('map-pin') : ''} ${esc(s.location.label)}</a>`;
      elLoc.style.display = '';
    } else { elLoc.style.display = 'none'; }
    // Carousel + people only show when the sponsor actually has them, so the
    // simpler entries get a clean logo/description card, no empty placeholders.
    const imgs = (s.images || []).filter(Boolean);
    if (elCarousel) elCarousel.style.display = imgs.length ? '' : 'none';
    if (imgs.length) renderCarousel(s);
    const ppl = (s.people || []).filter(p => p && (p.name || p.photo)).slice(0, 2);
    if (elPeopleLabel) elPeopleLabel.style.display = ppl.length ? '' : 'none';
    elPeople.style.display = ppl.length ? '' : 'none';
    elPeople.innerHTML = ppl.map(p =>
      `<div class="sponsor-person">${p.photo
        ? `<img class="sponsor-person-photo" src="${esc(p.photo)}" alt="${esc(p.name)}" />`
        : `<div class="sponsor-person-photo placeholder">${esc(initials(p.name))}</div>`}` +
      `<div class="sponsor-person-name">${esc(p.name)}</div>` +
      `<div class="sponsor-person-role">${esc(p.role)}</div></div>`).join('');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  }
  function close() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  slotsWrap.addEventListener('click', e => {
    const btn = e.target.closest('.sponsor-slot'); if (!btn) return;
    const s = SPONSORS.find(x => x.id === btn.dataset.id);
    if (s) open(s);
  });
  modal.querySelectorAll('[data-sponsor-close]').forEach(el => el.addEventListener('click', close));
  document.addEventListener('keydown', e => {
    if (!modal.classList.contains('open')) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowLeft') go(slide - 1);
    if (e.key === 'ArrowRight') go(slide + 1);
  });
})();
