/* ============================================================
   ICONS — central SVG icon library
   ------------------------------------------------------------
   A single source of clean, monochrome line icons (Lucide /
   Feather style) used in place of emojis across the site, so the
   visual tone stays consistent and serious.

   Usage
   -----
   1. Markup placeholder (auto-replaced on load):
        <i data-icon="calendar"></i>
        <i data-icon="calendar" style="color:var(--blush)"></i>

   2. Programmatic string (e.g. when building HTML in JS):
        Icons.svg('calendar')            -> '<svg …>…</svg>'
        Icons.svg('calendar', 'my-class')

   Icons inherit `currentColor`, so colour them with normal CSS
   (`color:` on the element or a parent). Default render size is
   1em square, so they sit naturally inline with text.
   ============================================================ */
(function (global) {
  'use strict';

  // Raw inner geometry for each icon, drawn on a 24×24 grid.
  // stroke-based so they stay crisp at any size and inherit colour.
  const P = {
    calendar:   '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    'map-pin':  '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    map:        '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Z"/><path d="M9 4v14M15 6v14"/>',
    book:       '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5Z"/><path d="M4 19h15"/>',
    pencil:     '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z"/>',
    note:       '<path d="M4 4h16v12l-4 4H4Z"/><path d="M16 20v-4h4M8 9h8M8 13h5"/>',
    users:      '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13A4 4 0 0 1 16 11"/>',
    user:       '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
    handshake:  '<path d="m11 17 2 2a1 1 0 0 0 1.4 0l3.6-3.6"/><path d="m14 14 2.5 2.5a1 1 0 0 0 1.4 0l1.6-1.6a1 1 0 0 0 0-1.4L16 9"/><path d="M3 11 8 6l3 1 4 4"/><path d="m3 11 2.5 2.5a1 1 0 0 0 1.4 0L11 9"/>',
    'bar-chart':'<path d="M3 3v18h18"/><rect x="7" y="11" width="3" height="7"/><rect x="12" y="7" width="3" height="11"/><rect x="17" y="13" width="3" height="5"/>',
    'trending-up':   '<path d="m3 17 6-6 4 4 8-8"/><path d="M17 7h4v4"/>',
    'trending-down': '<path d="m3 7 6 6 4-4 8 8"/><path d="M17 17h4v-4"/>',
    refresh:    '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    trophy:     '<path d="M6 4h12v4a6 6 0 0 1-12 0V4Z"/><path d="M6 6H3v1a4 4 0 0 0 3 3.9M18 6h3v1a4 4 0 0 1-3 3.9"/><path d="M10 16h4M9 20h6M12 16v4"/>',
    medal:      '<circle cx="12" cy="14" r="6"/><path d="M12 11v0M9 8 6 2M15 8l3-6"/><path d="m12 12 .8 1.7 1.7.2-1.3 1.2.4 1.7-1.6-.9-1.6.9.4-1.7-1.3-1.2 1.7-.2Z"/>',
    coffee:     '<path d="M4 8h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V8Z"/><path d="M17 9h2a2 2 0 0 1 0 4h-2"/><path d="M8 2v2M12 2v2"/>',
    utensils:   '<path d="M4 3v7a2 2 0 0 0 2 2v9M7 3v9M5 3v4M17 3c-1.5 0-3 1.5-3 4s1.5 3 3 3v11"/>',
    calculator: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2M12 11h2M16 11h0M8 15h2M12 15h2M16 15v2"/>',
    'triangle-ruler': '<path d="M5 4v14a1 1 0 0 0 1 1h14L5 4Z"/><path d="M9 14v1M9 11v1M13 16h1M16 16h1"/>',
    ruler:      '<path d="M3 9 9 3l12 12-6 6L3 9Z"/><path d="M7 8 8.5 9.5M10 6 12 8M13 9l1.5 1.5M9 12l2 2"/>',
    search:     '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    puzzle:     '<path d="M9 4a2 2 0 0 1 4 0c0 .7.4 1 1 1h2a1 1 0 0 1 1 1v2c0 .6.3 1 1 1a2 2 0 0 1 0 4c-.7 0-1 .4-1 1v3a1 1 0 0 1-1 1h-3c-.6 0-1-.3-1-1a2 2 0 0 0-4 0c0 .7-.4 1-1 1H5a1 1 0 0 1-1-1v-3c0-.6-.4-1-1-1a2 2 0 0 1 0-4c.7 0 1-.4 1-1V6a1 1 0 0 1 1-1h2c.7 0 1-.3 1-1Z"/>',
    target:     '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
    brain:      '<path d="M9.5 3A2.5 2.5 0 0 0 7 5.5v.3A3 3 0 0 0 5 11a3 3 0 0 0 1 4.5V17a3 3 0 0 0 4 2.8 2.5 2.5 0 0 0 2-2.3V5.5A2.5 2.5 0 0 0 9.5 3Z"/><path d="M14.5 3A2.5 2.5 0 0 1 17 5.5v.3A3 3 0 0 1 19 11a3 3 0 0 1-1 4.5V17a3 3 0 0 1-4 2.8 2.5 2.5 0 0 1-2-2.3"/>',
    flame:      '<path d="M12 2c1 4 5 5 5 9a5 5 0 0 1-10 0c0-1.5.7-2.7 1.5-3.5C9 9 9.5 7 9 5c2 1 2.5 2.5 3 4 .8-1.2 1-3 0-7Z"/>',
    dollar:     '<path d="M12 2v20"/><path d="M17 6.5C17 4.6 14.8 4 12 4S7 4.9 7 7.2 9 10 12 10.5 17 11.7 17 14s-2.5 3-5 3-5-.7-5-2.5"/>',
    money:      '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9v6M18 9v6"/>',
    clock:      '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    hourglass:  '<path d="M6 3h12M6 21h12"/><path d="M7 3c0 4 4 5 5 9 1-4 5-5 5-9M7 21c0-4 4-5 5-9 1 4 5 5 5 9"/>',
    'graduation-cap': '<path d="M22 9 12 5 2 9l10 4 10-4Z"/><path d="M6 11v5c0 1.5 2.7 3 6 3s6-1.5 6-3v-5"/>',
    school:     '<path d="M3 21h18M5 21V10l7-5 7 5v11"/><path d="M9 21v-5h6v5M10 11h4"/>',
    building:   '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M9 7h.01M9 11h.01M9 15h.01M15 7h.01M15 11h.01M15 15h.01"/>',
    bank:       '<path d="M3 10 12 4l9 6"/><path d="M4 10v8M9 10v8M15 10v8M20 10v8M3 21h18"/>',
    zap:        '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
    scale:      '<path d="M12 3v18M7 21h10"/><path d="M6 6h12M6 6 3 13a3 3 0 0 0 6 0L6 6Zm12 0-3 7a3 3 0 0 0 6 0l-3-7Z"/>',
    link:       '<path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/>',
    equals:     '<path d="M5 9h14M5 15h14"/>',
    hash:       '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
    shapes:     '<path d="M12 3 4 8v8l8 5 8-5V8l-8-5Z"/><path d="M4 8l8 5 8-5M12 13v8"/>',
    wrench:     '<path d="M15 4a5 5 0 0 0-5.9 6.6L3 16.7 7.3 21l6.1-6.1A5 5 0 0 0 20 9l-3 3-2-2 3-3a5 5 0 0 0-3-3Z"/>',
    ban:        '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
    feather:    '<path d="M20 4a7 7 0 0 0-10 0L4 10v6h6l6-6a7 7 0 0 0 4-6Z"/><path d="M4 20 13 11M10 9h4v4"/>',
    heart:      '<path d="M12 20s-7-4.5-9.5-9A5 5 0 0 1 12 6a5 5 0 0 1 9.5 5C19 15.5 12 20 12 20Z"/>',
    star:       '<path d="m12 3 2.6 5.5 6 .9-4.3 4.2 1 6L12 17l-5.3 2.6 1-6L3.4 9.4l6-.9L12 3Z"/>',
    check:      '<path d="m4 12 5 5L20 6"/>',
    'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    x:          '<path d="M6 6l12 12M18 6 6 18"/>',
    'x-circle': '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    tent:       '<path d="M3 20h18L12 4 3 20Z"/><path d="M12 4v16M12 11l5 9M12 11l-5 9"/>',
    lock:       '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    shield:     '<path d="M12 3 5 6v5c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6l-7-3Z"/>',
    sparkles:   '<path d="M12 3v6M12 15v6M3 12h6M15 12h6"/><path d="m6 6 3 3M15 15l3 3M18 6l-3 3M9 15l-3 3"/>',
    megaphone:  '<path d="m3 11 14-6v14L3 13v-2Z"/><path d="M3 11H2v2h1M7 13v4a2 2 0 0 0 4 0v-1"/>',
    clipboard:  '<rect x="5" y="4" width="14" height="18" rx="2"/><rect x="9" y="2" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h4"/>',
    mail:       '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    phone:      '<path d="M5 3h4l2 5-3 2a12 12 0 0 0 5 5l2-3 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2Z"/>',
    camera:     '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7 9.5 4h5L16 7"/><circle cx="12" cy="13" r="3.5"/>',
    music:      '<path d="M9 18V6l11-2v12"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    leaf:       '<path d="M11 20A7 7 0 0 1 4 13c0-5 4-9 16-9 0 12-4 16-9 16a7 7 0 0 1-7-7Z"/><path d="M4 20c4-6 8-8 13-9"/>',
    tree:       '<path d="M12 2 5 11h4l-4 6h6v5h2v-5h6l-4-6h4L12 2Z"/>',
    'mouse-pointer': '<path d="m4 4 7 16 2-7 7-2L4 4Z"/>',
    globe:      '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 4 5.5 4 9s-1.5 6.5-4 9c-2.5-2.5-4-5.5-4-9s1.5-6.5 4-9Z"/>',
    package:    '<path d="m3 8 9-5 9 5v8l-9 5-9-5V8Z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    backpack:   '<path d="M6 8a6 6 0 0 1 12 0v11a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8Z"/><path d="M9 8V5a3 3 0 0 1 6 0v3M9 14h6"/>',
    ticket:     '<path d="M3 8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2 2 2 0 0 0 0 4 2 2 0 0 1-2 2H5a2 2 0 0 1-2-2 2 2 0 0 0 0-4Z"/><path d="M14 6v12"/>',
    droplet:    '<path d="M12 3c3 4 6 7 6 11a6 6 0 0 1-12 0c0-4 3-7 6-11Z"/>',
    apple:      '<path d="M12 7c-2-3-7-2-7 3 0 4 3 11 5 11 1 0 1.5-.7 2-.7s1 .7 2 .7c2 0 5-7 5-11 0-5-5-6-7-3Z"/><path d="M12 7c0-2 1-4 3-4"/>',
    car:        '<path d="M5 13 7 7h10l2 6"/><path d="M3 13h18v5h-2a2 2 0 0 1-4 0H9a2 2 0 0 1-4 0H3v-5Z"/>',
    bus:        '<rect x="4" y="4" width="16" height="14" rx="2"/><path d="M4 11h16M8 18v2M16 18v2M8 8h8"/><circle cx="8" cy="15" r="0.5"/><circle cx="16" cy="15" r="0.5"/>',
    smile:      '<circle cx="12" cy="12" r="9"/><path d="M8 14a4 4 0 0 0 8 0M9 9h.01M15 9h.01"/>',
    gift:       '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v9h14v-9M12 8v13"/><path d="M12 8C12 5 9 4 8 5.5S9 8 12 8Zm0 0c0-3 3-4 4-2.5S15 8 12 8Z"/>',
    file:       '<path d="M6 3h8l4 4v14H6V3Z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
    video:      '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    mic:        '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6"/>',
    bag:        '<path d="M6 7h12l1 13H5L6 7Z"/><path d="M9 7a3 3 0 0 1 6 0"/>',
    construction: '<rect x="3" y="9" width="18" height="11" rx="1"/><path d="m4 9 4-4h8l4 4M9 9v11M15 9v11"/>',
    alert:      '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 9v5M12 17h.01"/>',
    spider:     '<circle cx="12" cy="12" r="3"/><path d="M12 9V5M9 11 5 9M9 13l-4 2M15 11l4-2M15 13l4 2M10 15l-2 4M14 15l2 4"/>',
    web:        '<path d="M12 2v20M2 12h20M5 5l14 14M19 5 5 19"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="9"/>',
    wand:       '<path d="m4 20 12-12M14 4l1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z"/>',
    'arrow-right': '<path d="M5 12h14M13 6l6 6-6 6"/>',
    'arrow-left':  '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    'arrow-up':    '<path d="M12 19V5M6 11l6-6 6 6"/>',
    'arrows-h':    '<path d="M8 7 4 11l4 4M16 7l4 4-4 4M4 11h16"/>',
  };

  const VIEW = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';

  function svg(name, cls) {
    const inner = P[name];
    if (!inner) return '';
    const c = cls ? ` class="${cls}"` : '';
    return `<svg${c} ${VIEW} width="1em" height="1em" aria-hidden="true" focusable="false">${inner}</svg>`;
  }

  // Replace every <… data-icon="name"> placeholder with its SVG.
  function render(root) {
    (root || document).querySelectorAll('[data-icon]').forEach(el => {
      const out = svg(el.dataset.icon, el.dataset.iconClass);
      if (out) { el.innerHTML = out; el.classList.add('icon'); }
    });
  }

  const Icons = { svg, render, names: () => Object.keys(P) };

  // Expose globally and (when modules are used) as an export.
  global.Icons = Icons;
  if (typeof module !== 'undefined' && module.exports) module.exports = Icons;

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => render());
    } else {
      render();
    }
  }
})(typeof window !== 'undefined' ? window : this);
