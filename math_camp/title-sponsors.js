/* ============================================================
   TITLE SPONSORS — shared across the whole site.
   Renders the sponsor logo slots beside the navbar logo on EVERY
   page, plus the pop-out details modal. Edit the SPONSORS array
   below to add / remove sponsors (up to 4 look best).

   Asset paths are ABSOLUTE (served from the site root, e.g.
   '/Staples_Canada_logo_2018.png') so they resolve correctly from
   any page depth (home, /about/, /support/, …). Drop image/logo
   files in the site root and reference them with a leading '/'.

   Leave `logo` empty ('') to render the sponsor's name as a
   branded wordmark instead of an image. Leave `images` empty to
   show labelled placeholder boxes in the carousel.
   ============================================================ */
(function () {
  if (window.__hgSponsorsInit) return;   // guard against double-inclusion
  window.__hgSponsorsInit = true;

  const SPONSORS = [
    {
      id: 'staples',
      name: 'Staples',
      tagline: 'Work. Learn. Grow.',
      brandColor: '#CC0000',                            // Staples red — themes the modal accents
      logo: '/Staples_Canada_logo_2018.png',            // official Staples logo (tagline built in)
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
      name: 'Game Time',
      tagline: 'Cards, Collectibles & Games',
      brandColor: '#1E6FB8',                            // Game Time crest blue — themes the modal accents
      logo: '/gametime-logo.jpg',                       // goat-crest logo
      description:
        'Game Time is a local cards, collectibles, and games shop — the community’s '
        + 'spot for Pokémon, Yu-Gi-Oh!, Magic: The Gathering, and more, from the newest '
        + 'trading card releases to board games, miniatures, and accessories. '
        + '“Home of the GOATs,” Game Time is proud to be a title sponsor of HigherGrade '
        + 'Tutoring Summer Camp 2026 and to support local students. Find them online at '
        + 'itsgametime.ca or call 905-822-9609.',
      location: {
        label: 'Mississauga, Ontario',
        url: 'https://www.google.com/maps/search/?api=1&query=Game+Time+Cards+Collectibles+and+Games+Mississauga',
      },
      images: [
        '/gametime-storefront.png',
        '/gametime-store.jpg',
        '/gametime-magic.jpg',
        '/gametime-pokemon.jpg',
      ],
      people: [],                                       // add up to 2 { name, role, photo } when you have them
    },
    {
      id: 'maplestaple',
      name: 'The Maple Staple',
      tagline: 'For bookworms, by passionate writers',
      brandColor: '#DD5A34',                            // Maple Staple orange
      logo: '/maplestaple-logo.png',
      description:
        'The Maple Staple is a bookstore for bookworms, by passionate writers — a home '
        + 'for readers across every genre, from romance and fantasy to mystery, young '
        + 'adult, and children’s books, plus their own maple staple magazine and a '
        + 'community of authors. The Maple Staple is proud to support HigherGrade '
        + 'Tutoring Summer Camp 2026 and to champion young readers and writers. Visit '
        + 'themaplestaple.com or call (888) 426-9236.',
      location: {
        label: 'themaplestaple.com',
        url: 'https://themaplestaple.com',
      },
      images: [
        '/maplestaple-1.jpg',
        '/maplestaple-2.jpg',
        '/maplestaple-3.jpg',
        '/maplestaple-4.jpg',
      ],
      people: [],                                       // add up to 2 { name, role, photo } when you have them
    },
    // Add one more sponsor object here when you have it.
  ];

  const MODAL_HTML =
    '<div class="sponsor-modal" id="hg-sponsor-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-label="Sponsor details">'
    + '<div class="sponsor-modal-backdrop" data-sponsor-close></div>'
    + '<div class="sponsor-modal-card" role="document">'
    + '<button class="sponsor-modal-close" data-sponsor-close aria-label="Close">×</button>'
    + '<div class="sponsor-modal-head">'
    + '<img class="sponsor-modal-logo" id="sm-logo" alt="" />'
    + '<div><div class="sponsor-modal-eyebrow">Title Sponsor</div>'
    + '<h2 class="sponsor-modal-name" id="sm-name"></h2>'
    + '<p class="sponsor-modal-tagline" id="sm-tagline"></p></div>'
    + '</div>'
    + '<div class="sponsor-carousel">'
    + '<button class="sponsor-carousel-arrow prev" id="sm-prev" aria-label="Previous image">‹</button>'
    + '<div class="sponsor-carousel-viewport"><div class="sponsor-carousel-track" id="sm-track"></div></div>'
    + '<button class="sponsor-carousel-arrow next" id="sm-next" aria-label="Next image">›</button>'
    + '</div>'
    + '<div class="sponsor-carousel-dots" id="sm-dots"></div>'
    + '<div class="sponsor-location" id="sm-location"></div>'
    + '<div class="sponsor-modal-desc" id="sm-desc"></div>'
    + '<div class="sponsor-people-label">In partnership with</div>'
    + '<div class="sponsor-people" id="sm-people"></div>'
    + '</div></div>';

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  const initials = n => (n || '?').trim().split(/\s+/).map(w => w[0] || '').slice(0, 2).join('').toUpperCase();

  function build() {
    if (!SPONSORS.length) return;

    const navInner = document.querySelector('.navbar .nav-inner') || document.querySelector('.nav-inner');
    if (!navInner) return;   // no site navbar on this page (e.g. app views) — skip

    // Find or build the left cluster (logo + sponsors), mirroring the home page.
    let slotsWrap = document.getElementById('title-sponsors');
    if (!slotsWrap) {
      const logo = navInner.querySelector('.nav-logo');
      let left = navInner.querySelector('.nav-left');
      if (!left && logo) {
        // Wrap the existing logo in a .nav-left so the sponsors sit beside it.
        left = document.createElement('div');
        left.className = 'nav-left';
        logo.parentNode.insertBefore(left, logo);
        left.appendChild(logo);
      }
      slotsWrap = document.createElement('div');
      slotsWrap.className = 'title-sponsors';
      slotsWrap.id = 'title-sponsors';
      slotsWrap.setAttribute('aria-label', 'Title sponsors');
      (left || navInner).appendChild(slotsWrap);
    }

    // Inject the modal once. (Own id so it never collides with a page's
    // existing #sponsor-modal — e.g. the Support page's tier-inquiry form.)
    let modal = document.getElementById('hg-sponsor-modal');
    if (!modal) {
      const holder = document.createElement('div');
      holder.innerHTML = MODAL_HTML;
      modal = holder.firstElementChild;
      document.body.appendChild(modal);
    }

    wire(slotsWrap, modal);
  }

  function wire(slotsWrap, modal) {
    const elCard  = modal.querySelector('.sponsor-modal-card');
    const elLogo  = modal.querySelector('#sm-logo');
    const elName  = modal.querySelector('#sm-name');
    const elTag   = modal.querySelector('#sm-tagline');
    const elTrack = modal.querySelector('#sm-track');
    const elDots  = modal.querySelector('#sm-dots');
    const elLoc   = modal.querySelector('#sm-location');
    const elDesc  = modal.querySelector('#sm-desc');
    const elPeople= modal.querySelector('#sm-people');
    const elPrev  = modal.querySelector('#sm-prev');
    const elNext  = modal.querySelector('#sm-next');

    let slide = 0;

    /* ── Render the navbar slots ── */
    slotsWrap.innerHTML = SPONSORS.map(s => `
      <button class="sponsor-slot" data-id="${esc(s.id)}" title="${esc(s.name)} — click for details">
        ${s.logo
          ? `<img src="${esc(s.logo)}" alt="${esc(s.name)}" class="sponsor-slot-img" />`
          : `<span class="sponsor-slot-wordmark" style="color:${esc(s.brandColor || 'var(--text)')}">${esc(s.name)}</span>`}
      </button>`).join('');

    /* ── Carousel ── */
    function renderCarousel(s) {
      const imgs = (s.images || []).slice(0, 4);
      if (!imgs.length) imgs.push('');
      elTrack.innerHTML = imgs.map((src, i) => `
        <div class="sponsor-slide">
          ${src
            ? `<img src="${esc(src)}" alt="${esc(s.name)} photo ${i + 1}" />`
            : `<div class="sponsor-slide-placeholder">Image ${i + 1}<br><small>add a photo</small></div>`}
        </div>`).join('');
      elDots.innerHTML = imgs.map((_, i) =>
        `<button class="sponsor-dot" data-i="${i}" aria-label="Go to image ${i + 1}"></button>`).join('');
      slide = 0;
      update();
    }
    function update() {
      elTrack.style.transform = `translateX(-${slide * 100}%)`;
      [...elDots.children].forEach((d, i) => d.classList.toggle('active', i === slide));
    }
    function go(n) {
      const count = elTrack.children.length;
      slide = (n + count) % count;
      update();
    }
    elPrev.addEventListener('click', () => go(slide - 1));
    elNext.addEventListener('click', () => go(slide + 1));
    elDots.addEventListener('click', e => {
      const d = e.target.closest('.sponsor-dot'); if (d) go(+d.dataset.i);
    });

    /* ── Open / close ── */
    function open(s) {
      elCard.style.setProperty('--sponsor-accent', s.brandColor || 'var(--blush)');
      if (s.logo) {
        elLogo.src = s.logo; elLogo.style.display = '';
        elName.style.display = 'none';
        elTag.style.display  = 'none';
      } else {
        elLogo.style.display = 'none';
        elName.style.display = '';
        elTag.style.display  = '';
        elName.style.color = s.brandColor || '';
      }
      elName.textContent = s.name || '';
      elTag.textContent  = s.tagline || '';
      elDesc.textContent = s.description || '';
      if (s.location && s.location.label) {
        elLoc.innerHTML = `<a href="${esc(s.location.url)}" target="_blank" rel="noopener">📍 ${esc(s.location.label)}</a>`;
        elLoc.style.display = '';
      } else {
        elLoc.style.display = 'none';
      }
      elPeople.innerHTML = (s.people || []).slice(0, 2).map(p => `
        <div class="sponsor-person">
          ${p.photo
            ? `<img class="sponsor-person-photo" src="${esc(p.photo)}" alt="${esc(p.name)}" />`
            : `<div class="sponsor-person-photo placeholder">${esc(initials(p.name))}</div>`}
          <div class="sponsor-person-name">${esc(p.name)}</div>
          <div class="sponsor-person-role">${esc(p.role)}</div>
        </div>`).join('');
      // Hide the "In partnership with" label when there are no people yet.
      const peopleLabel = modal.querySelector('.sponsor-people-label');
      if (peopleLabel) peopleLabel.style.display = (s.people && s.people.length) ? '' : 'none';
      renderCarousel(s);
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
      if (!s) return;
      // On the Support page the sponsor bubbles expose a richer "showcase"
      // reveal (colour floods the page, the logo forms, then the card opens).
      // Clicking a navbar sponsor there runs that same reveal — just without
      // popping an actual bubble. Elsewhere, open the plain modal.
      if (typeof window.__hgSponsorShowcase === 'function') {
        window.__hgSponsorShowcase(s);
      } else {
        open(s);
      }
    });
    modal.querySelectorAll('[data-sponsor-close]').forEach(el =>
      el.addEventListener('click', close));
    document.addEventListener('keydown', e => {
      if (!modal.classList.contains('open')) return;
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowLeft') go(slide - 1);
      if (e.key === 'ArrowRight') go(slide + 1);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
