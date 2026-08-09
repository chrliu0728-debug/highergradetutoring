// Shared chrome: the nav bar, the footer, and a few helpers every page uses.
//
// Nav and footer labels live here so there is one place to rename a page.
// Body copy lives in the HTML, each block carrying a data-tag="..." label.
// Append ?tags=1 to any URL to see those tags drawn on the page.

const NAV = [
  { href: '/index.html', label: 'Home' },
  { href: '/about.html', label: 'About Us' },
  { href: '/support.html', label: 'Support Us' },
  { href: '/contact.html', label: 'Contact' },
  { href: '/booking.html', label: 'Book a Detail', cta: true },
];

const FOOTER_COLUMNS = [
  {
    title: 'Pages',
    links: [
      { href: '/index.html', label: 'Home' },
      { href: '/about.html', label: 'About Us' },
      { href: '/support.html', label: 'Support Us' },
      { href: '/contact.html', label: 'Contact' },
    ],
  },
  {
    title: 'Booking',
    links: [
      { href: '/booking.html', label: 'Start a booking' },
      { href: '/portal.html', label: 'Track my booking' },
      { href: '/booking.html#packages', label: 'Packages & pricing' },
      { href: '/contact.html#faq', label: 'Booking FAQ' },
    ],
  },
  {
    title: 'Crew',
    links: [
      { href: '/staff.html', label: 'Staff dashboard' },
      { href: '/support.html#partners', label: 'Partners' },
      { href: '/contact.html', label: 'Careers' },
    ],
  },
];

function currentPath() {
  const p = location.pathname;
  return p === '/' || p === '' ? '/index.html' : p;
}

function renderNav() {
  const host = document.querySelector('[data-site-nav]');
  if (!host) return;
  const here = currentPath();

  host.className = 'nav';
  host.innerHTML = `
    <div class="nav-inner">
      <a class="brand" href="/index.html">
        <img src="/logo.jpg" alt="Kan Detailings logo" />
        <span class="brand-name" data-tag="global.brand.name">Kan Detailings
          <span class="brand-sub" data-tag="global.brand.tagline">Orbital-grade car care</span>
        </span>
      </a>
      <button class="nav-toggle" aria-label="Toggle navigation" aria-expanded="false">☰</button>
      <nav class="nav-links">
        ${NAV.map(
          (item) =>
            `<a href="${item.href}" class="${item.cta ? 'cta' : ''}${
              here === item.href ? ' active' : ''
            }">${item.label}</a>`
        ).join('')}
      </nav>
    </div>`;

  const toggle = host.querySelector('.nav-toggle');
  const links = host.querySelector('.nav-links');
  toggle.addEventListener('click', () => {
    const open = links.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(open));
  });
}

function renderFooter() {
  const host = document.querySelector('[data-site-footer]');
  if (!host) return;
  const year = new Date().getFullYear();

  host.className = 'footer';
  host.innerHTML = `
    <div class="wrap">
      <div class="footer-grid">
        <div>
          <h4>Kan Detailings</h4>
          <p data-tag="global.footer.blurb" class="small">
            Placeholder paragraph about a detailing crew that treats every panel like the hull of a
            long-haul ship. Swap this text for the real story whenever you like.
          </p>
        </div>
        ${FOOTER_COLUMNS.map(
          (col) => `
          <div>
            <h4>${col.title}</h4>
            <ul>${col.links.map((l) => `<li><a href="${l.href}">${l.label}</a></li>`).join('')}</ul>
          </div>`
        ).join('')}
      </div>
      <div class="footer-bottom">
        <span data-tag="global.footer.legal">© ${year} Kan Detailings. Placeholder legal line.</span>
        <span class="mono" data-tag="global.footer.contact">hello@kandetailings.example · (000) 000-0000</span>
      </div>
    </div>`;
}

/** ?tags=1 draws each data-tag label on the page so copy is easy to locate. */
function maybeShowTags() {
  if (new URLSearchParams(location.search).get('tags') === '1') {
    document.body.classList.add('show-tags');
  }
}

// ------------------------------------------------------------- helpers ----

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const res = await fetch(path, {
    method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body is fine */
  }
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
  return data;
}

export function money(amount) {
  return `$${Number(amount).toLocaleString('en-CA')}`;
}

export function timeAgo(iso) {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

renderNav();
renderFooter();
maybeShowTags();
