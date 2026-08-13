/*
  THE SPIDER — the thing waiting at the exit.
  ------------------------------------------------------------------
  Three phases, and the shape of each one is a rhythm rather than a
  screen:

    1. DODGE. A question every 2–6 seconds, three seconds on the clock.
       Solve it and the web goes past you. Miss it and the web hits the
       glass and stays there, taking a piece of what you can see with it.
       Twenty rounds.

    2. MARCUS. He turns up mid-fight to tell you the Lime Sword is the
       only thing that touches it. The webs do NOT stop while he talks —
       you read and dodge at the same time, which is the joke.

    3. BOTH. A dodge every two seconds on a 2.5s clock, and a second
       question running alongside it that is your swing. Fourteen clean
       hits kills it, if the misses go your way.

  The server owns every decision that matters: which door is right, when
  the window shuts, whether a swing lands or crits, what a web costs.
  This file owns the clock and the mess on the screen.

  Timing note: each question is fetched at the moment its round starts,
  never pre-fetched, so the window a camper sees is the window the server
  is measuring. The server adds a fixed grace to cover the round trip —
  see BOSS_GRACE_MS — so a slow connection costs you a fraction of a
  second rather than the fight.
*/
(function () {
  'use strict';

  const API = {
    get:  (p)     => fetch(p, { credentials: 'same-origin' }).then(r => r.json()),
    post: (p, b)  => fetch(p, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(b || {}),
    }).then(r => r.json()),
  };

  const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
  const fmt = n => Number(n || 0).toLocaleString();
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const rnd  = (a, b) => a + Math.random() * (b - a);

  let el = null;         // the root overlay
  let state = null;      // last server state
  let stop = false;      // set when the fight is over or the tab is leaving
  // One loop per fight, ever. start() and resume() can both plausibly fire
  // on the same page load — the exit handler calls one and boot calls the
  // other — and two loops answering the same fight burn twenty rounds in
  // seconds, because each one keeps resolving the other's questions.
  let running = false;

  /* ── styles ──────────────────────────────────────────────────── */
  function styles() {
    if (document.getElementById('boss-css')) return;
    const s = document.createElement('style');
    s.id = 'boss-css';
    s.textContent = `
    .boss-root{position:fixed; inset:0; z-index:2400; background:#05030a;
      color:#F5F3FF; font-family:'Inter',system-ui,sans-serif; overflow:hidden;
      display:flex; flex-direction:column; align-items:center; justify-content:center}
    .boss-root.fading{animation:bossFade 1.6s ease forwards}
    @keyframes bossFade{from{opacity:0}to{opacity:1}}
    .boss-stage{position:relative; width:100%; height:100%;
      display:flex; flex-direction:column; align-items:center; justify-content:center}

    /* The spider. Big, and it breathes. */
    .boss-spider{position:absolute; top:6%; left:50%; transform:translateX(-50%);
      width:min(52vh,440px); max-width:78vw; pointer-events:none; z-index:1;
      filter:drop-shadow(0 0 40px rgba(120,40,180,.55));
      animation:bossBreathe 3.4s ease-in-out infinite}
    @keyframes bossBreathe{0%,100%{transform:translateX(-50%) scale(1)}
      50%{transform:translateX(-50%) scale(1.045)}}
    .boss-spider.lunge{animation:bossLunge .5s ease}
    @keyframes bossLunge{0%{transform:translateX(-50%) scale(1)}
      40%{transform:translateX(-50%) scale(1.22)}100%{transform:translateX(-50%) scale(1)}}
    .boss-spider.hurt{filter:drop-shadow(0 0 40px rgba(163,230,53,.9)) brightness(1.6)}

    /* Webs that stuck. Each one eats a piece of the screen. */
    .boss-webs{position:absolute; inset:0; pointer-events:none; z-index:60}
    .boss-web{position:absolute; width:230px; height:230px; opacity:.9;
      transform:translate(-50%,-50%) rotate(var(--r,0deg)); animation:webHit .35s ease}
    @keyframes webHit{from{transform:translate(-50%,-50%) scale(.2) rotate(var(--r,0deg))}
      to{transform:translate(-50%,-50%) scale(1) rotate(var(--r,0deg))}}
    .boss-web-fly{position:absolute; z-index:59; width:54px; height:54px;
      pointer-events:none}

    /* HUD */
    .boss-hud{position:absolute; top:0; left:0; right:0; z-index:70;
      display:flex; gap:14px; align-items:center; justify-content:space-between;
      padding:14px 18px; background:linear-gradient(#05030acc,transparent)}
    .boss-bar{flex:1; max-width:340px}
    .boss-bar .lab{font-size:.68rem; letter-spacing:.12em; text-transform:uppercase;
      opacity:.7; margin-bottom:4px; display:flex; justify-content:space-between}
    .boss-bar .track{height:11px; border-radius:99px; background:#1c1030; overflow:hidden;
      border:1px solid #33204f}
    .boss-bar .fill{height:100%; transition:width .3s ease}
    .boss-bar.you .fill{background:linear-gradient(90deg,#4ade80,#a3e635)}
    .boss-bar.you.hurt .fill{background:linear-gradient(90deg,#f59e0b,#ef4444)}
    .boss-bar.them .fill{background:linear-gradient(90deg,#a855f7,#ec4899)}
    .boss-phase{font-size:.72rem; letter-spacing:.16em; text-transform:uppercase;
      opacity:.75; white-space:nowrap}

    /* Question cards */
    .boss-cards{position:absolute; left:0; right:0; bottom:0; z-index:80;
      display:flex; gap:16px; justify-content:center; align-items:flex-end;
      padding:0 16px 26px; flex-wrap:wrap}
    .boss-card{width:min(430px,94vw); background:rgba(20,10,36,.94);
      border:1px solid #4c1d95; border-radius:16px; padding:14px 16px 16px;
      box-shadow:0 20px 60px rgba(0,0,0,.6); backdrop-filter:blur(6px)}
    .boss-card.attack{border-color:#65a30d; background:rgba(18,26,8,.94)}
    .boss-card .kind{font-size:.64rem; letter-spacing:.16em; text-transform:uppercase;
      opacity:.75; margin-bottom:6px; display:flex; justify-content:space-between}
    .boss-card .q{font-size:1rem; font-weight:600; line-height:1.35; margin-bottom:10px;
      min-height:2.6em}
    .boss-doors{display:flex; gap:10px}
    .boss-door{flex:1; padding:13px 8px; border-radius:11px; cursor:pointer;
      border:1.5px solid #5b21b6; background:#2a1150; color:#fff; font-size:1rem;
      font-weight:700; font-family:inherit; transition:transform .08s, background .15s}
    .boss-card.attack .boss-door{border-color:#4d7c0f; background:#1a2e05}
    .boss-door:hover:not(:disabled){transform:translateY(-2px); background:#3b1a6b}
    .boss-door:disabled{opacity:.45; cursor:default}
    .boss-door.right{background:#166534; border-color:#22c55e}
    .boss-door.wrong{background:#7f1d1d; border-color:#ef4444}
    .boss-timer{height:5px; border-radius:99px; background:#1c1030; margin-top:10px;
      overflow:hidden}
    .boss-timer i{display:block; height:100%; width:100%;
      background:linear-gradient(90deg,#facc15,#ef4444); transform-origin:left}
    .boss-idle{opacity:.55; font-size:.82rem; text-align:center; padding:22px 0 6px}

    /* Marcus, mid-fight */
    .boss-marcus{position:absolute; left:50%; bottom:calc(50% - 40px);
      transform:translateX(-50%); z-index:85; width:min(560px,94vw);
      background:rgba(12,4,24,.97); border:1px solid #7c3aed; border-radius:16px;
      padding:16px 18px; box-shadow:0 24px 70px rgba(0,0,0,.7)}
    .boss-marcus .who{font-size:.68rem; letter-spacing:.18em; text-transform:uppercase;
      color:#c4b5fd; margin-bottom:7px}
    .boss-marcus p{font-size:.98rem; line-height:1.55; min-height:3.2em; margin:0}
    .boss-marcus .next{text-align:right; font-size:.78rem; opacity:.6; margin-top:8px}

    /* Banners */
    .boss-banner{position:absolute; inset:0; z-index:120; display:flex;
      flex-direction:column; align-items:center; justify-content:center; gap:18px;
      background:rgba(5,3,10,.93); text-align:center; padding:24px}
    .boss-banner h1{font-size:clamp(1.6rem,5vw,2.6rem); margin:0}
    .boss-banner p{max-width:560px; line-height:1.6; opacity:.85; margin:0}
    .boss-btn{padding:12px 26px; border-radius:11px; border:0; cursor:pointer;
      font-weight:700; font-size:.95rem; font-family:inherit;
      background:linear-gradient(135deg,#a3e635,#65a30d); color:#0b1400}
    .boss-btn.ghost{background:transparent; border:1px solid #6b21a8; color:#e9d5ff}
    .boss-err{color:#fca5a5; font-size:.86rem; min-height:1.2em}
    .boss-flash{position:fixed; inset:0; background:rgba(190,24,93,.32); z-index:110;
      opacity:0; transition:opacity .1s; pointer-events:none}
    .boss-flash.on{opacity:1}
    @media (max-width:760px){
      .boss-card .q{font-size:.94rem}
      .boss-spider{top:3%; width:min(38vh,300px)}
    }`;
    document.head.appendChild(s);
  }

  /* An SVG web — no asset needed, and it scales cleanly. */
  function webSvg(size) {
    return `<svg viewBox="0 0 100 100" width="${size}" height="${size}"
      xmlns="http://www.w3.org/2000/svg">
      <g fill="none" stroke="rgba(233,228,255,.82)" stroke-width="1.4">
        ${[0,45,90,135,180,225,270,315].map(a => {
          const r = (a * Math.PI) / 180;
          return `<line x1="50" y1="50" x2="${50 + 48 * Math.cos(r)}"
                   y2="${50 + 48 * Math.sin(r)}"/>`;
        }).join('')}
        ${[12, 22, 32, 42].map(rad => `<polygon points="${
          [0,45,90,135,180,225,270,315].map(a => {
            const r = (a * Math.PI) / 180;
            return `${50 + rad * Math.cos(r)},${50 + rad * Math.sin(r)}`;
          }).join(' ')}"/>`).join('')}
      </g></svg>`;
  }

  function spiderSvg() {
    // Drawn rather than loaded: the fight has to work even if an image 404s.
    return `<svg viewBox="0 0 240 200" xmlns="http://www.w3.org/2000/svg">
      <g stroke="#1c0f2e" stroke-width="7" stroke-linecap="round" fill="none">
        <path d="M96 96 L38 56 L10 78"/><path d="M96 108 L32 106 L4 128"/>
        <path d="M98 120 L40 148 L18 178"/><path d="M104 130 L74 168 L60 196"/>
        <path d="M144 96 L202 56 L230 78"/><path d="M144 108 L208 106 L236 128"/>
        <path d="M142 120 L200 148 L222 178"/><path d="M136 130 L166 168 L180 196"/>
      </g>
      <ellipse cx="120" cy="126" rx="52" ry="46" fill="#231038"/>
      <ellipse cx="120" cy="120" rx="44" ry="38" fill="#2f1550"/>
      <ellipse cx="120" cy="82" rx="30" ry="26" fill="#1c0f2e"/>
      <g fill="#f43f5e">
        <circle cx="108" cy="76" r="6"/><circle cx="132" cy="76" r="6"/>
        <circle cx="99" cy="88" r="4"/><circle cx="141" cy="88" r="4"/>
        <circle cx="114" cy="92" r="3"/><circle cx="126" cy="92" r="3"/>
      </g>
      <path d="M104 100 L112 110 M136 100 L128 110" stroke="#0b0614" stroke-width="5"
        stroke-linecap="round"/>
    </svg>`;
  }

  /* ── the overlay ─────────────────────────────────────────────── */
  function mount() {
    styles();
    el = document.createElement('div');
    el.className = 'boss-root fading';
    el.innerHTML = `
      <div class="boss-stage">
        <div class="boss-spider" id="bspider">${spiderSvg()}</div>
        <div class="boss-webs" id="bwebs"></div>
        <div class="boss-hud">
          <div class="boss-bar you" id="byou">
            <div class="lab"><span>You</span><span id="byouv"></span></div>
            <div class="track"><i class="fill" id="byoufill"></i></div>
          </div>
          <div class="boss-phase" id="bphase"></div>
          <div class="boss-bar them">
            <div class="lab"><span>The spider</span><span id="bthemv"></span></div>
            <div class="track"><i class="fill" id="bthemfill"></i></div>
          </div>
        </div>
        <div class="boss-cards" id="bcards"></div>
      </div>
      <div class="boss-flash" id="bflash"></div>`;
    document.body.appendChild(el);
    document.body.style.overflow = 'hidden';
  }

  function unmount() {
    if (el && el.parentNode) el.parentNode.removeChild(el);
    document.body.style.overflow = '';
    el = null;
  }

  const $ = id => el && el.querySelector('#' + id);

  function paint() {
    if (!el || !state) return;
    const hpPct = Math.max(0, (state.hp / state.maxHp) * 100);
    const spPct = Math.max(0, (state.spiderHp / state.spiderMaxHp) * 100);
    $('byoufill').style.width = hpPct + '%';
    $('bthemfill').style.width = spPct + '%';
    $('byouv').textContent = fmt(state.hp) + ' / ' + fmt(state.maxHp);
    $('bthemv').textContent = state.phase >= 3
      ? fmt(state.hits) + ' / ' + fmt(state.hitsToKill) + ' hits' : '???';
    $('byou').classList.toggle('hurt', hpPct <= 40);
    $('bphase').textContent = state.phase === 1
      ? `Phase 1 · dodge · ${state.round}/${state.p1Rounds}`
      : state.phase === 2 ? 'Phase 2 · keep dodging'
      : 'Phase 3 · dodge and swing';
  }

  function flash() {
    const f = $('bflash'); if (!f) return;
    f.classList.add('on'); setTimeout(() => f.classList.remove('on'), 110);
  }

  /* A web flies in from the spider. If it wasn't dodged it sticks. */
  function throwWeb(landed) {
    const webs = $('bwebs'); if (!webs) return;
    const sp = $('bspider');
    sp.classList.add('lunge'); setTimeout(() => sp.classList.remove('lunge'), 500);
    const fly = document.createElement('div');
    fly.className = 'boss-web-fly';
    fly.innerHTML = webSvg(54);
    const endX = 12 + Math.random() * 76, endY = 26 + Math.random() * 54;
    fly.style.left = '50%'; fly.style.top = '18%';
    webs.appendChild(fly);
    const anim = fly.animate([
      { transform: 'translate(-50%,-50%) scale(.5)', opacity: .9 },
      { transform: `translate(${(endX - 50) * (window.innerWidth / 100)}px,
                              ${(endY - 18) * (window.innerHeight / 100)}px)
                    scale(${landed ? 2.6 : 3.4})`,
        opacity: landed ? 1 : 0 },
    ], { duration: 420, easing: 'ease-in' });
    anim.onfinish = () => {
      fly.remove();
      if (!landed) return;
      // It stuck. Part of the screen is gone for the rest of the fight.
      const w = document.createElement('div');
      w.className = 'boss-web';
      w.style.left = endX + '%'; w.style.top = endY + '%';
      w.style.setProperty('--r', Math.floor(Math.random() * 360) + 'deg');
      w.innerHTML = webSvg(230);
      webs.appendChild(w);
      flash();
    };
  }

  /* ── one question on screen ──────────────────────────────────── */
  function card(kind, q, windowMs, onPick) {
    const cards = $('bcards');
    const node = document.createElement('div');
    node.className = 'boss-card' + (kind === 'attack' ? ' attack' : '');
    node.innerHTML = `
      <div class="kind"><span>${kind === 'attack' ? '🗡 Swing' : '🕸 Dodge'}</span>
        <span>${esc(q.unit || '')}</span></div>
      <div class="q">${esc(q.question)}</div>
      <div class="boss-doors">
        <button class="boss-door" data-side="L">${esc(q.left)}</button>
        <button class="boss-door" data-side="R">${esc(q.right)}</button>
      </div>
      <div class="boss-timer"><i></i></div>`;
    cards.appendChild(node);

    const bar = node.querySelector('.boss-timer i');
    bar.animate([{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }],
      { duration: windowMs, easing: 'linear', fill: 'forwards' });

    let done = false;
    const finish = (side, btn) => {
      if (done) return; done = true;
      node.querySelectorAll('.boss-door').forEach(b => { b.disabled = true; });
      onPick(side, btn, node);
    };
    node.querySelectorAll('.boss-door').forEach(b =>
      b.addEventListener('click', () => finish(b.dataset.side, b)));
    // The window closing counts as a miss, and is resolved the same way.
    const timer = setTimeout(() => finish(null, null), windowMs + 90);
    node._cancel = () => { clearTimeout(timer); done = true; };
    return node;
  }

  async function askAndResolve(kind, windowFallback) {
    const q = await API.post('/api/dungeon/boss/question', { kind });
    if (!q.ok) return null;
    const windowMs = q.data.windowMs || windowFallback;
    return new Promise(resolve => {
      const node = card(kind, q.data, windowMs, async (side, btn) => {
        const r = await API.post('/api/dungeon/boss/answer',
          { kind, side: side || 'X' });
        if (!r.ok) { node.remove(); return resolve(null); }
        const d = r.data;
        state = d.state;
        if (btn) btn.classList.add(d.solved ? 'right' : 'wrong');
        if (kind === 'dodge') throwWeb(d.web === 'hit');
        if (kind === 'attack' && (d.swing === 'hit' || d.swing === 'crit')) {
          const sp = $('bspider');
          sp.classList.add('hurt');
          setTimeout(() => sp.classList.remove('hurt'), 260);
        }
        paint();
        setTimeout(() => node.remove(), 520);
        resolve(d);
      });
    });
  }

  function idle(text) {
    const cards = $('bcards');
    const n = document.createElement('div');
    n.className = 'boss-idle';
    n.textContent = text;
    cards.appendChild(n);
    return n;
  }

  /* ── Marcus, without stopping the fight ──────────────────────── */
  function marcusSpeak(lines) {
    return new Promise(resolve => {
      const box = document.createElement('div');
      box.className = 'boss-marcus';
      box.innerHTML = `<div class="who">Marcus</div><p></p>
        <div class="next">click to continue ▾</div>`;
      el.querySelector('.boss-stage').appendChild(box);
      const p = box.querySelector('p');
      let i = -1, typing = null, shown = 0, full = '';

      function type() {
        if (shown >= full.length) { typing = null; return; }
        shown += 1;
        p.textContent = full.slice(0, shown);
        typing = setTimeout(type, 17);
      }
      function next() {
        if (typing) { clearTimeout(typing); typing = null; shown = full.length;
                      p.textContent = full; return; }
        i += 1;
        if (i >= lines.length) {
          box.remove();
          document.removeEventListener('click', onClick, true);
          return resolve();
        }
        full = lines[i]; shown = 0; p.textContent = '';
        type();
      }
      function onClick(e) {
        // Clicks on a door belong to the fight, not to Marcus.
        if (e.target.closest('.boss-door')) return;
        e.stopPropagation(); next();
      }
      document.addEventListener('click', onClick, true);
      next();
    });
  }

  /* ── phases ──────────────────────────────────────────────────── */
  async function phase1() {
    while (!stop && state.phase === 1 && state.round < state.p1Rounds) {
      const d = await askAndResolve('dodge', state.dodgeWindowMs);
      if (!d) return;
      if (state.outcome) return;
      await wait(rnd(state.gapMinMs, state.gapMaxMs));
    }
  }

  async function phase2() {
    // Only advance if we're actually coming out of phase 1. Resuming a
    // fight that was already in phase 2 must not try to advance again.
    if (state.phase === 1) {
      const r = await API.post('/api/dungeon/boss/phase', { phase: 2 });
      if (r.ok) { state = r.data; paint(); }
    }

    // The webs keep coming while he talks. That's the point of phase 2.
    let talking = true;
    (async () => {
      while (!stop && talking && !state.outcome) {
        await askAndResolve('dodge', state.dodgeWindowMs);
        if (state.outcome) return;
        await wait(rnd(state.gapMinMs, state.gapMaxMs));
      }
    })();

    await marcusSpeak([
      "Don't stop moving. It's still throwing those.",
      'Listen — you can hit it all day with what you\'re holding and it will not care.',
      'That thing is an Arachnid. There is exactly one thing in this camp built to cut one.',
      'The Lime Sword. The one that fell apart and you put back together.',
      'Open your bag and *equip it*. Nothing else will leave a mark.',
      'I\'ll be here. Try not to get wrapped up while you do it.',
    ]);
    talking = false;
  }

  async function phase3() {
    // Gate: the sword has to actually be equipped, which is what Marcus
    // just spent phase 2 saying. Skipped entirely when we're resuming a
    // fight that had already reached phase 3.
    while (state.phase < 3) {
      const r = await API.post('/api/dungeon/boss/phase', { phase: 3 });
      if (r.ok) { state = r.data; paint(); break; }
      const again = await confirmGate(r.error);
      if (!again) return;
    }

    let attacking = false;
    while (!stop && !state.outcome) {
      const dodge = askAndResolve('dodge', state.p3DodgeWindowMs);
      if (!attacking) {
        attacking = true;
        askAndResolve('attack', state.p3AttackWindowMs).then(() => {
          attacking = false;
        });
      }
      await dodge;
      if (state.outcome) break;
      await wait(state.p3GapMs);
    }
  }

  function confirmGate(message) {
    return new Promise(resolve => {
      const b = document.createElement('div');
      b.className = 'boss-banner';
      b.innerHTML = `
        <h1>🗡 Equip the Lime Sword</h1>
        <p>${esc(message || 'Nothing else touches it.')}</p>
        <p style="opacity:.6">Open your inventory in another tab, equip the Lime
           Sword to your weapon slot, then come back and press ready.</p>
        <div style="display:flex; gap:10px; flex-wrap:wrap; justify-content:center">
          <button class="boss-btn" id="bready">I've equipped it</button>
          <button class="boss-btn ghost" id="bgiveup">Give up</button>
        </div>`;
      el.appendChild(b);
      b.querySelector('#bready').addEventListener('click', () => {
        b.remove(); resolve(true);
      });
      b.querySelector('#bgiveup').addEventListener('click', async () => {
        b.remove(); stop = true;
        await API.post('/api/dungeon/boss/flee', {});
        resolve(false);
        finish(false);
      });
    });
  }

  /* ── the end ─────────────────────────────────────────────────── */
  async function worldSpiderPrompt() {
    await marcusSpeak([
      'It\'s down. I genuinely did not think that would work.',
      'Alright. There\'s a thing I have to tell you, and you\'re not going to like it.',
      'That wasn\'t the World Spider. That was one of its legs, near enough.',
      'The World Spider is underneath all of this. It IS all of this.',
      'The camp, the dungeon, the points, the whole gameverse — it\'s spun out of that thing.',
      'You can kill it. You\'re holding the only blade that could.',
      'But if it dies, the web goes with it. All of it.',
      'The camp. Everything you built. Me.',
      'So. Do you want to kill the World Spider?',
    ]);

    return new Promise(resolve => {
      const b = document.createElement('div');
      b.className = 'boss-banner';
      b.innerHTML = `
        <h1>Kill the World Spider?</h1>
        <p>Everything HigherGrade Tutoring is made of is spun out of it.
           If it dies, that goes too.</p>
        <div style="display:flex; gap:12px; flex-wrap:wrap; justify-content:center">
          <button class="boss-btn" id="bwsyes">Yes</button>
          <button class="boss-btn ghost" id="bwsno">No</button>
        </div>
        <div class="boss-err" id="bwserr"></div>`;
      el.appendChild(b);
      const err = b.querySelector('#bwserr');

      b.querySelector('#bwsno').addEventListener('click', async () => {
        // There is no no. The server refuses it and we come straight back
        // to the same two buttons.
        const r = await API.post('/api/dungeon/boss/world-spider', { choice: 'no' });
        err.textContent = (r && r.error) || 'bug detected, user input incorrect';
      });
      b.querySelector('#bwsyes').addEventListener('click', async () => {
        b.querySelectorAll('button').forEach(x => { x.disabled = true; });
        err.textContent = '';
        const r = await API.post('/api/dungeon/boss/world-spider', { choice: 'yes' });
        if (!r.ok) {
          err.textContent = r.error || 'It refused.';
          b.querySelectorAll('button').forEach(x => { x.disabled = false; });
          return;
        }
        b.remove();
        resolve(r.data);
      });
    });
  }

  async function ending(data) {
    // Everything goes quiet, then there's a note.
    const stage = el.querySelector('.boss-stage');
    stage.style.transition = 'opacity 2.6s ease';
    stage.style.opacity = '0';
    await wait(2800);

    let flipped = false;
    const b = document.createElement('div');
    b.className = 'boss-banner';
    b.style.background = '#05030a';
    el.appendChild(b);

    function draw() {
      b.innerHTML = `
        <div style="max-width:480px; width:100%; background:#faf7ef; color:#1a1410;
             border-radius:6px; padding:34px 28px; text-align:center;
             box-shadow:0 30px 90px rgba(0,0,0,.8); transform:rotate(-.6deg);
             font-family:Georgia,serif; font-size:1.15rem; line-height:1.7;
             min-height:150px; display:flex; align-items:center; justify-content:center">
          ${esc(flipped ? data.note.back : data.note.text)}
        </div>
        <button class="boss-btn ghost" id="bflip">
          ${flipped ? 'Turn it back' : 'Turn it over'}</button>
        <p style="opacity:.55; font-size:.84rem">
          ${fmt(data.pointsGained)} points from everything you were carrying.
          The note is in your inventory.</p>
        <button class="boss-btn" id="bdone">…</button>`;
      b.querySelector('#bflip').addEventListener('click', () => {
        flipped = !flipped; draw();
      });
      b.querySelector('#bdone').addEventListener('click', () => {
        location.href = '/student-portal/student-portal.html';
      });
    }
    draw();
  }

  function finish(won) {
    const b = document.createElement('div');
    b.className = 'boss-banner';
    b.innerHTML = `
      <h1>${won ? 'It stopped moving.' : 'It got you.'}</h1>
      <p>${won ? 'Something else is moving underneath.'
               : 'You wake up at the dungeon door with everything you were carrying. '
                 + 'It will be waiting the next time you try to leave.'}</p>
      <button class="boss-btn" id="bout">Back to the dashboard</button>`;
    el.appendChild(b);
    b.querySelector('#bout').addEventListener('click', () => {
      location.href = '/student-portal/student-portal.html';
    });
  }

  /* ── run ─────────────────────────────────────────────────────── */
  async function run(initial) {
    if (running) return;
    running = true;
    state = initial;
    stop = false;
    mount();
    paint();
    await wait(1500);           // the fade, before anything moves

    try {
      // Pick up wherever the fight actually is. A refresh in the middle of
      // phase 3 used to drop the camper back into Marcus's speech.
      if (state.phase <= 1 && !state.outcome) await phase1();
      if (state.phase <= 2 && !stop && !state.outcome) await phase2();
      if (!stop && !state.outcome) await phase3();
    } catch (e) {
      // A dropped connection shouldn't strand anyone inside the overlay.
      console.error('boss fight ended early', e);
    }

    if (stop) return;
    if (state.outcome === 'won') {
      const data = await worldSpiderPrompt();
      if (data) await ending(data);
      return;
    }
    finish(false);
  }

  window.addEventListener('beforeunload', () => {
    if (state && !state.outcome) {
      navigator.sendBeacon && navigator.sendBeacon('/api/dungeon/boss/flee');
    }
  });

  window.HGBoss = {
    /** Start a fight from the state the exit call handed back. */
    start: run,
    /** Resume one that was left open (a refresh mid-fight). */
    async resume() {
      if (running) return true;
      const r = await API.get('/api/dungeon/boss');
      if (r.ok && r.data && !r.data.outcome) { run(r.data); return true; }
      return false;
    },
  };
})();
