/* ============================================================
   MARCUS — the visual-novel guide to the Lime Trial.
   ----------------------------------------------------------------
   A camper who opened the Discord chest walks away with the Calamity
   Catalyst role and no idea what to do with it. Marcus is the fix: a
   VN-style dialogue box that turns up when a Catalyst holder is signed
   in, tells them what they let out, and walks them to the trial.

   Three scenes, one per stop, held in localStorage per student:

     stage 0  intro    — wherever they are when they sign in. Ends by
                         pointing at Home.
     stage 1  home     — the sword is hidden in a bubble, in plain
                         sight, with all the other bubbles. Points at
                         Support.
     stage 2  support  — the small thing he forgot to mention: the
                         sword is in pieces. Points at the lime bubble.
     stage 3  done     — he stays quiet, but still points at the bubble
                         on the Support page.

   Off the target page, Marcus doesn't talk — the nav link for the stop
   he wants just pulses until they take it. A scene the camper has
   already sat through shows a "press Esc to skip" chip; Esc always
   works either way.
   ============================================================ */
(function () {
  'use strict';

  const CATALYST_ROLE = 'calamity_catalyst';
  const KEY  = 'hg.marcus.v1';      // + '.' + studentId
  const NAME = 'Marcus';

  // Drop a real portrait in here (any URL) and it replaces the drawn one.
  const PORTRAIT = null;

  const TYPE_MS  = 17;   // per character
  const PAUSE_MS = 110;  // extra beat after a sentence ends

  /* ── The script ─────────────────────────────────────────────
     *asterisks* italicise a word. Keep his voice: breezy, guilty,
     over-explains, apologises for none of it. */
  const SCENES = {
    intro: [
      "Oh — you're up. Good. I was starting to think she'd got you already.",
      "So. That chest you opened. I think you might've just released the world spider, man.",
      "She'd been locked in there for ages. Centuries, probably. Nobody writes these things down properly, which is its own kind of crime.",
      "Yeah — *she*. Everyone says he. She thinks that's very funny, right up until the moment she stops thinking things are funny.",
      "Here's the bit I'd rather you heard from me than from, you know. Experience.",
      "She spins people. Into cocoons. Tidy ones, actually — there's real craft in it, I'd compliment her if she wasn't going to eat me.",
      "And then she drinks them. For breakfast. Which is the rudest possible meal to pick, because you can't even skip it.",
      "...You're not *into* that, are you? The cocoon thing. Being someone's breakfast.",
      "Because if you are, I'd like it on the record that I'll be standing quite far away from you for the rest of this quest~",
      "Anyway! Name's Marcus. I'll be helping you on this one. It is, I suppose, *technically* my fault you were standing near that chest to begin with~",
      "Well. It is what it is. You'd be down to make up for your mistakes, right? I mean. I hope so. At the very least.",
      "There's a sword. It cuts her up a decent bit. That's the technical description, I checked.",
      "And to get a legendary weapon, we must take on legendary methods. Called: exploration!",
      "Common common, let's go — back to the home page. I'll light the button up for you.",
      "And don't log out. I'm not doing this speech twice.",
    ],
    home: [
      "There we go. The home page. Smaller than I remember, but then so is everything.",
      "Right. The sword. I hid it.",
      "In a bubble.",
      "...With all the other bubbles. Over on the support page. Right out in plain sight, where nobody would ever look — because nobody ever looks at the thing that's already in front of them.",
      "I'd call that genius, except I then spent a *very* long afternoon failing to find it again, and I've been told genius doesn't usually involve that.",
      "You'll know the one. It's the bubble that looks like it's hiding something, which in hindsight rather undermines the whole plan.",
      "Support page. I'll flash the button. Off you go~",
    ],
    support: [
      "See? Bubbles. Told you. Right out in the open the whole time.",
      "Now. Small thing. Tiny, really. I've been meaning to bring it up since the chest and the moment kept not arriving.",
      "The sword's broken.",
      "Not *broken* broken. Shattered. Into limes. It's a long story and you were, arguably, a participant.",
      "So before you can point it at anything with eight legs and an appetite, it has to be put back together. By you. I'll supervise, which is the hard part.",
      "Pop the bubble when you're ready. The pieces fall fast and they rot faster — cut them out of the air and the blade remembers its own shape.",
      "Cut *all* of them and, well. People notice that sort of thing~",
      "Go on. She's not getting any less hungry while we chat.",
    ],
  };

  /* Which scene belongs to which stop, and where that stop lives. */
  const STOPS = [
    { scene: 'intro',   page: null,      next: 'home'    },
    { scene: 'home',    page: 'home',    next: 'support' },
    { scene: 'support', page: 'support', next: 'bubble'  },
  ];

  const HOME_HREFS    = ['/index.html', 'index.html', '/'];
  const SUPPORT_MATCH = '/support/support.html';

  /* ── Where are we ───────────────────────────────────────────── */
  function onHome() {
    const p = location.pathname.replace(/\/+$/, '');
    return p === '' || p === '/index.html';
  }
  function onSupport() { return location.pathname.endsWith(SUPPORT_MATCH); }
  function onPage(which) { return which === 'home' ? onHome() : which === 'support' ? onSupport() : false; }

  /* ── Progress ───────────────────────────────────────────────── */
  function load(sid) {
    try {
      const raw = localStorage.getItem(KEY + '.' + sid);
      const o = raw ? JSON.parse(raw) : null;
      if (o && typeof o.stage === 'number') return { stage: o.stage, seen: o.seen || {} };
    } catch (_) { /* private mode, or someone edited it by hand */ }
    return { stage: 0, seen: {} };
  }
  function save(sid, state) {
    try { localStorage.setItem(KEY + '.' + sid, JSON.stringify(state)); } catch (_) {}
  }

  /* ── Styles ─────────────────────────────────────────────────── */
  function injectStyles() {
    if (document.getElementById('marcus-style')) return;
    const s = document.createElement('style');
    s.id = 'marcus-style';
    s.textContent = `
.marcus-wrap{position:fixed;inset:0;z-index:2147483000;display:flex;
  align-items:flex-end;justify-content:center;padding:0 0 22px;
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  background:linear-gradient(180deg,rgba(4,10,2,0) 40%,rgba(4,10,2,.55));
  animation:marcus-in .35s ease both;cursor:pointer;-webkit-tap-highlight-color:transparent}
@keyframes marcus-in{from{opacity:0}to{opacity:1}}
.marcus-wrap.out{animation:marcus-out .3s ease both}
@keyframes marcus-out{to{opacity:0}}

.marcus-box{position:relative;width:min(860px,calc(100% - 32px));
  display:flex;gap:18px;align-items:flex-end;
  animation:marcus-rise .42s cubic-bezier(.2,.9,.3,1.05) both}
@keyframes marcus-rise{from{transform:translateY(26px);opacity:0}to{transform:none;opacity:1}}

/* Portrait sits above the plate, VN-style, half overlapping it. */
.marcus-face{flex:0 0 auto;width:150px;margin-bottom:-6px;pointer-events:none;
  filter:drop-shadow(0 14px 30px rgba(0,0,0,.55));
  animation:marcus-bob 4.5s ease-in-out infinite}
@keyframes marcus-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
.marcus-face img,.marcus-face svg{width:100%;height:auto;display:block}

.marcus-plate{flex:1 1 auto;min-width:0;position:relative;
  background:linear-gradient(180deg,rgba(20,34,10,.97),rgba(9,18,4,.98));
  border:2px solid rgba(163,230,53,.5);border-radius:18px!important;
  padding:20px 24px 22px;color:#ECFCCB;
  box-shadow:0 26px 70px rgba(0,0,0,.6),0 0 46px rgba(132,204,22,.16)}
.marcus-name{position:absolute;top:-15px;left:22px;
  background:linear-gradient(135deg,#BEF264,#65A30D);color:#14300A;
  font:900 .82rem/1 system-ui,sans-serif;letter-spacing:.1em;text-transform:uppercase;
  padding:8px 16px;border-radius:999px!important;
  box-shadow:0 6px 18px rgba(101,163,13,.5)}
.marcus-text{margin:10px 0 0;font-size:1.02rem;line-height:1.65;color:#E9FBCD;
  min-height:3.3em;white-space:pre-wrap}
.marcus-text em{color:#D9F99D;font-style:italic;font-weight:700}
.marcus-cursor{display:inline-block;width:.5em;color:#A3E635;animation:marcus-blink 1s steps(1) infinite}
@keyframes marcus-blink{50%{opacity:0}}

/* The ▾ that says "there's more". */
.marcus-next{position:absolute;right:18px;bottom:12px;color:#A3E635;
  font-size:1.1rem;line-height:1;animation:marcus-nudge 1.1s ease-in-out infinite;opacity:0}
.marcus-next.show{opacity:1}
@keyframes marcus-nudge{0%,100%{transform:translateY(0)}50%{transform:translateY(4px)}}
.marcus-dots{position:absolute;right:20px;top:-11px;display:flex;gap:5px}
.marcus-dots i{width:6px;height:6px;border-radius:50%!important;
  background:rgba(163,230,53,.28);transition:background .2s}
.marcus-dots i.on{background:#A3E635}
/* Top-right of the overlay, not under the plate — anchored to the bottom it
   fell off the screen. It lives outside .marcus-box on purpose: that box is
   animated, which would make it the containing block for a fixed child. */
.marcus-skip{position:absolute;top:18px;right:20px;
  border:1px solid rgba(163,230,53,.4);background:rgba(6,16,2,.75);color:#A3E635;
  font:800 .72rem/1 system-ui,sans-serif;letter-spacing:.1em;text-transform:uppercase;
  padding:8px 16px;border-radius:999px!important;cursor:pointer;
  -webkit-appearance:none;appearance:none}
.marcus-skip:hover{background:rgba(163,230,53,.16);color:#D9F99D}

/* The nav link Marcus wants taken next. The ring is drawn INSIDE the link
   (negative offset, inset shadow) because .sidenav clips its overflow —
   anything painted outside the link's own box gets cut off in there. */
.marcus-flash{position:relative;border-radius:10px!important;
  outline:2px solid rgba(163,230,53,.9);outline-offset:-3px;
  color:#D9F99D!important;
  animation:marcus-glow 1.25s ease-in-out infinite}
@keyframes marcus-glow{
  0%,100%{background:rgba(163,230,53,.10);box-shadow:inset 0 0 12px rgba(163,230,53,.25)}
  50%{background:rgba(163,230,53,.26);box-shadow:inset 0 0 26px rgba(163,230,53,.7)}}

/* The lime bubble, once he's pointed at it. */
.marcus-target{animation:marcus-target 1.2s ease-in-out infinite!important}
@keyframes marcus-target{0%,100%{filter:none}50%{filter:brightness(1.25) drop-shadow(0 0 26px rgba(190,242,100,1))}}
.marcus-chip{position:fixed;right:20px;bottom:20px;z-index:2147482000;
  max-width:min(300px,calc(100% - 40px));
  background:linear-gradient(180deg,rgba(20,34,10,.97),rgba(9,18,4,.98));
  border:2px solid rgba(163,230,53,.5);border-radius:14px!important;
  padding:13px 16px;color:#D9F99D;font:600 .86rem/1.5 system-ui,sans-serif;
  box-shadow:0 18px 44px rgba(0,0,0,.5);animation:marcus-in .4s ease both}
.marcus-chip b{color:#BEF264;display:block;font-size:.72rem;letter-spacing:.12em;
  text-transform:uppercase;margin-bottom:4px}

@media (max-width:720px){
  .marcus-dots{display:none}   /* fifteen of them do not fit next to a name plate */
  .marcus-box{gap:10px;align-items:flex-end}
  .marcus-face{width:88px;margin-bottom:-4px}
  .marcus-plate{padding:18px 16px 20px}
  .marcus-text{font-size:.95rem;min-height:5em}
  .marcus-name{font-size:.72rem;padding:7px 12px;left:14px}
}
@media (prefers-reduced-motion:reduce){
  .marcus-face,.marcus-next,.marcus-flash,.marcus-target{animation:none!important}
}`;
    document.head.appendChild(s);
  }

  /* A drawn stand-in until there's real art: a hooded traveller, lime rim
     light, caught mid-shrug about the whole spider situation. Set PORTRAIT
     above to swap in a real drawing. */
  const FACE_SVG = `
<svg viewBox="0 0 150 190" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="mg-cloak" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#3F6212"/><stop offset="100%" stop-color="#14210A"/>
    </linearGradient>
    <linearGradient id="mg-hood" x1=".2" y1="0" x2=".8" y2="1">
      <stop offset="0%" stop-color="#65A30D"/><stop offset="100%" stop-color="#243D08"/>
    </linearGradient>
    <linearGradient id="mg-skin" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#F0D0AC"/><stop offset="100%" stop-color="#C98F63"/>
    </linearGradient>
  </defs>
  <!-- neck, then shoulders over it so the chin sits on a collar -->
  <path d="M64 128h22v24H64z" fill="#B77E52"/>
  <path d="M4 190c5-34 28-52 71-52s66 18 71 52z" fill="url(#mg-cloak)"/>
  <!-- head -->
  <ellipse cx="75" cy="93" rx="34" ry="39" fill="url(#mg-skin)"/>
  <!-- hood: a ring around the face, open at the front -->
  <path d="M75 20c33 0 55 26 55 63 0 26-9 45-23 55l-9-19c9-11 14-24 14-39 0-26-14-42-37-42s-37 16-37 42c0 15 5 28 14 39l-9 19C29 128 20 109 20 83c0-37 22-63 55-63z" fill="url(#mg-hood)"/>
  <!-- the brim's shadow across the brow -->
  <path d="M43 76c7-19 18-28 32-28s25 9 32 28c-9-11-19-16-32-16s-23 5-32 16z" fill="#1B2E08" opacity=".55"/>
  <!-- eyes: one lid lower than the other, because he is like that -->
  <ellipse cx="63" cy="92" rx="4" ry="4.6" fill="#22310F"/>
  <ellipse cx="88" cy="92" rx="4" ry="4.2" fill="#22310F"/>
  <circle cx="64.4" cy="90.4" r="1.4" fill="#F7FEE7"/>
  <circle cx="89.4" cy="90.4" r="1.4" fill="#F7FEE7"/>
  <path d="M56 83c4-3 9-3 13-1" stroke="#4A3520" stroke-width="2.6" fill="none" stroke-linecap="round"/>
  <path d="M82 81c4-2 9-2 13 1" stroke="#4A3520" stroke-width="2.6" fill="none" stroke-linecap="round"/>
  <!-- nose + a lopsided smirk -->
  <path d="M75 96c2 5 3 8 0 9" stroke="#A9724A" stroke-width="2" fill="none" stroke-linecap="round"/>
  <path d="M64 112c7 6 17 5 22-3" stroke="#8A5433" stroke-width="2.6" fill="none" stroke-linecap="round"/>
  <!-- lime rim light down the left of the hood -->
  <path d="M26 74c5-27 20-44 44-47" stroke="#BEF264" stroke-width="3.4" fill="none" stroke-linecap="round" opacity=".9"/>
  <path d="M126 84c1-24-7-40-20-49" stroke="#84CC16" stroke-width="2.6" fill="none" stroke-linecap="round" opacity=".5"/>
</svg>`;

  /* ── Line markup: *word* → <em>word</em> ────────────────────── */
  function parse(line) {
    const parts = [];
    const re = /\*([^*]+)\*/g;
    let last = 0, m;
    while ((m = re.exec(line))) {
      if (m.index > last) parts.push({ t: line.slice(last, m.index), em: false });
      parts.push({ t: m[1], em: true });
      last = m.index + m[0].length;
    }
    if (last < line.length) parts.push({ t: line.slice(last), em: false });
    return parts.length ? parts : [{ t: line, em: false }];
  }

  const reduced = () => window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── One scene, start to finish ─────────────────────────────── */
  function play(lines, opts, onEnd) {
    injectStyles();
    const wrap = document.createElement('div');
    wrap.className = 'marcus-wrap';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-label', NAME + ' is speaking');
    wrap.innerHTML = `
      <div class="marcus-box">
        <div class="marcus-face">${PORTRAIT
          ? `<img src="${PORTRAIT}" alt="${NAME}" />` : FACE_SVG}</div>
        <div class="marcus-plate">
          <div class="marcus-name">${NAME}</div>
          <div class="marcus-dots"></div>
          <p class="marcus-text"></p>
          <div class="marcus-next">▾</div>
        </div>
      </div>
      ${opts.skippable
        ? '<button class="marcus-skip" type="button">Press Esc to skip</button>' : ''}`;
    document.body.appendChild(wrap);

    const textEl = wrap.querySelector('.marcus-text');
    const nextEl = wrap.querySelector('.marcus-next');
    const dotsEl = wrap.querySelector('.marcus-dots');
    lines.forEach(() => dotsEl.appendChild(document.createElement('i')));
    const dots = Array.from(dotsEl.children);

    let i = -1;            // line being shown
    let spans = [];        // the current line, split by emphasis
    let full = '';         // its plain text
    let shown = 0;         // characters revealed
    let timer = null;
    let closed = false;

    function reveal(n) {
      shown = Math.min(n, full.length);
      let left = shown;
      for (const s of spans) {
        const take = Math.max(0, Math.min(s._len, left));
        s.textContent = s._text.slice(0, take);
        left -= take;
      }
      nextEl.classList.toggle('show', shown >= full.length);
    }

    function type() {
      if (closed) return;
      if (shown >= full.length) { nextEl.classList.add('show'); return; }
      reveal(shown + 1);
      const ch = full[shown - 1];
      timer = setTimeout(type, '.!?'.includes(ch) ? TYPE_MS + PAUSE_MS : TYPE_MS);
    }

    function line(n) {
      clearTimeout(timer);
      i = n;
      dots.forEach((d, k) => d.classList.toggle('on', k <= i));
      textEl.innerHTML = '';
      // full is the line WITHOUT the *emphasis* markers — those never get
      // typed, so counting them would stall the cursor on invisible chars.
      full = '';
      spans = parse(lines[i]).map(p => {
        const s = document.createElement(p.em ? 'em' : 'span');
        s._text = p.t; s._len = p.t.length;
        full += p.t;
        textEl.appendChild(s);
        return s;
      });
      shown = 0;
      nextEl.classList.remove('show');
      if (reduced()) reveal(full.length); else type();
    }

    function advance() {
      if (closed) return;
      if (shown < full.length) { clearTimeout(timer); reveal(full.length); return; }
      if (i + 1 < lines.length) line(i + 1); else finish();
    }

    function skip() { if (!closed) finish(); }

    function finish() {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      document.removeEventListener('keydown', onKey, true);
      wrap.classList.add('out');
      setTimeout(() => { wrap.remove(); if (onEnd) onEnd(); }, 300);
    }

    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); skip(); return; }
      if (e.key === ' ' || e.key === 'Enter' || e.key === 'ArrowRight') {
        e.preventDefault(); e.stopPropagation(); advance();
      }
    }
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('click', ev => {
      if (ev.target.closest('.marcus-skip')) { skip(); return; }
      advance();
    });

    line(0);
  }

  /* ── Pointing at things ─────────────────────────────────────── */
  function clearFlash() {
    document.querySelectorAll('.marcus-flash')
      .forEach(n => n.classList.remove('marcus-flash'));
  }

  function flashNav(which) {
    clearFlash();
    const links = Array.from(document.querySelectorAll('a[href]')).filter(a => {
      if (a.classList.contains('sidenav-logo')) return false;   // the logo is home too, but nobody reads it as a button
      const href = a.getAttribute('href') || '';
      return which === 'home'
        ? HOME_HREFS.includes(href)
        : href.endsWith(SUPPORT_MATCH);
    });
    links.forEach(a => a.classList.add('marcus-flash'));
    // On a narrow screen the sidenav is off-canvas, so the link Marcus is
    // pointing at isn't on screen — light up the hamburger that opens it.
    const toggle = document.getElementById('sidenav-toggle');
    if (toggle && links.length && getComputedStyle(toggle).display !== 'none') {
      toggle.classList.add('marcus-flash');
    }
  }

  /* The lime bubble belongs to lime-challenge.js and arrives on its own
     schedule, so watch for it rather than assume it's there. */
  function pointAtBubble() {
    let tries = 0;
    const tick = setInterval(() => {
      const b = document.querySelector('.lime-bubble');
      if (b) {
        clearInterval(tick);
        b.classList.add('marcus-target');
        chip('Marcus is pointing', 'The drifting lime bubble. That\'s the one — pop it.');
      } else if (++tries > 40) {
        clearInterval(tick);
      }
    }, 250);
  }

  function chip(title, body) {
    if (document.querySelector('.marcus-chip')) return;
    const c = document.createElement('div');
    c.className = 'marcus-chip';
    c.innerHTML = `<b>${title}</b>${body}`;
    document.body.appendChild(c);
    // It's done its job once the trial is open. Give up watching after a
    // couple of minutes rather than leave a timer running on the page.
    let n = 0;
    const watch = setInterval(() => {
      if (document.querySelector('.lime-stage')) { clearInterval(watch); c.remove(); }
      else if (++n > 300) clearInterval(watch);
    }, 400);
  }

  /* ── The router ─────────────────────────────────────────────── */
  function router(sid, state) {
    function step() {
      const stop = STOPS[state.stage];
      if (!stop) {                       // the quest is told — just point
        if (onSupport()) pointAtBubble();
        return;
      }
      if (stop.page && !onPage(stop.page)) { flashNav(stop.page); return; }
      if (document.querySelector('.lime-stage')) return;   // mid-trial, stay out of it

      clearFlash();
      play(SCENES[stop.scene], { skippable: !!state.seen[stop.scene] }, () => {
        state.seen[stop.scene] = 1;
        state.stage += 1;
        save(sid, state);
        step();                          // the next stop may be this very page
      });
    }
    step();
  }

  /* ── Role check ─────────────────────────────────────────────
     Same rule as lime-challenge.js: match the role by NAME as well as
     id, because a hand-made role can win the race for the unique name
     and leave the seeded id unused. */
  function idsNamed(name, seededId) {
    const want = String(name).toLowerCase().replace(/\s+/g, '');
    const out = new Set([seededId]);
    const all = (window.HG && window.HG.cache && window.HG.cache.roles) || [];
    for (const r of all) {
      if (String((r && r.name) || '').toLowerCase().replace(/\s+/g, '') === want) out.add(r.id);
    }
    return out;
  }

  function catalyst() {
    const me = window.HG && window.HG.cache && window.HG.cache.me
      ? window.HG.cache.me.student : null;
    if (!me || me.frozen) return null;   // frozen accounts get the payment overlay, not Marcus
    const roles = Array.isArray(me.roles) ? me.roles : [];
    const ids = idsNamed('Calamity Catalyst', CATALYST_ROLE);
    return roles.some(id => ids.has(id)) ? me : null;
  }

  /* ── Boot ───────────────────────────────────────────────────
     Signing in on the portal swaps the dashboard in without a reload,
     so a one-shot check at load would miss it. Poll instead, and stop
     the moment a Catalyst turns up. */
  (async function boot() {
    if (window.dataReady) { try { await window.dataReady; } catch (_) {} }
    let started = false;
    function check() {
      if (started) return true;
      const me = catalyst();
      if (!me) return false;
      started = true;
      injectStyles();
      router(me.id, load(me.id));
      return true;
    }
    if (check()) return;
    const tick = setInterval(() => { if (check()) clearInterval(tick); }, 900);
  })();
})();
