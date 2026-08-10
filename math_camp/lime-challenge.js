/* ============================================================
   THE LIME TRIAL — hidden mini-game on the Support Us page.
   ----------------------------------------------------------------
   A lime bubble drifts up the page, but only for a signed-in
   camper holding the Calamity Catalyst role. Clicking it floats
   the broken Lime Sword up out of the bubble, explains itself,
   and drops the player into a one-minute cutting trial: 200 limes
   fade in and out fast, and half of them have to be cut to reforge
   the blade. Missing is expected — the pass bar is a percentage,
   not a near-perfect run.

   Limes only land within arm's reach of the cursor, and never
   under the fixed sidenav — which draws over the stage.

   The sponsor bubbles behind stay live on purpose. Clicking one
   opens its sponsorship package right over the top of the run —
   the trial keeps counting down underneath. That's the hazard.
   ============================================================ */
(function () {
  'use strict';

  const CATALYST_ROLE = 'calamity_catalyst';
  const SWORD_ROLE    = 'lime_sword';

  const TOTAL     = 200;      // limes that appear across the run
  const PASS_PCT  = 0.5;      // the bar: cut half of whatever falls
  const TARGET    = Math.ceil(TOTAL * PASS_PCT);
  const LIMIT_MS  = 60000;    // the minute — a ceiling, not the pace
  const LIFE_MS   = 1000;     // how long one lime stays cuttable
  const BEST_HIT  = 1000;     // cut it the instant it lands
  const WORST_HIT = 400;      // cut it as it fades
  const FC_POINTS = 1000;     // the full-combo bounty — mirrors LIME_FC_POINTS

  /* How far from the cursor a lime is allowed to land. Limes used to drop
     anywhere on the page, so passing meant flinging the mouse corner to
     corner inside one lifetime — impossible at a 1s lifetime. Landing them
     in a ring around the cursor is what keeps a hard run fair: everything
     is reachable, you just can't reach all of it. */
  const REACH_MIN = 70;
  const REACH_MAX = 360;

  /* Spawn rate ramps 2 → 6 limes per second. Phases are written as a
     COUNT rather than a duration so the run always totals exactly TOTAL;
     each phase's length falls out as count / cps. The run lands around
     43s, leaving the last lime fading well inside the minute.

     The peak stops at 6 rather than 8 on purpose. Six limes sharing the
     ring for one second each is already past what most people can clear,
     but it stays inside what a hand can physically do — which matters now
     that a full combo pays out. A bar nobody can reach isn't a bar. */
  const PHASES = [
    { cps: 2, count: 10 },    //  5.00s
    { cps: 3, count: 15 },    //  5.00s
    { cps: 4, count: 30 },    //  7.50s
    { cps: 5, count: 45 },    //  9.00s
    { cps: 6, count: 100 },   // 16.67s — the blender
  ];

  let REFORGED = false;   // set at boot when the camper already holds the blade

  const MUSIC = '/alban_gogh-pinball-ball-372717.mp3';
  const IMG_LIME    = '/lime-slice.png';
  const IMG_BROKEN  = '/lime-sword-broken.png';
  const IMG_WHOLE   = '/lime-sword.png';

  /* ── Styles ─────────────────────────────────────────────────── */
  function injectStyles() {
    if (document.getElementById('lime-trial-style')) return;
    const el = document.createElement('style');
    el.id = 'lime-trial-style';
    el.textContent = `
/* The drifting bubble. Fixed rather than parked in the sponsor field,
   which gets wiped and rebuilt whenever the sponsor list reloads. */
.lime-bubble{
  position:fixed; left:var(--lx,12vw); bottom:-160px; z-index:900;
  width:var(--lsize,104px); height:var(--lsize,104px);
  background:none; border:0; padding:0; margin:0; cursor:pointer;
  -webkit-appearance:none; appearance:none; outline:none;
  animation:lime-rise var(--ldur,34s) linear var(--ldelay,-6s) infinite;
  will-change:transform,opacity;
}
@keyframes lime-rise{
  0%{transform:translate(0,0);opacity:0}
  4%{opacity:1} 94%{opacity:1}
  100%{transform:translate(var(--ldrift,30px),calc(-100vh - 220px));opacity:0}
}
.lime-bubble-inner{
  position:absolute; inset:0; border-radius:50%!important;
  background:radial-gradient(circle at 32% 26%,rgba(236,255,205,.9),rgba(190,240,120,.34) 60%,rgba(132,204,22,.18));
  border:2px solid rgba(190,242,100,.95);
  box-shadow:0 10px 30px rgba(101,163,13,.34),0 0 26px rgba(163,230,53,.6),
             inset 0 0 22px rgba(255,255,255,.5);
  display:flex; align-items:center; justify-content:center; overflow:hidden;
  animation:lime-throb 2.4s ease-in-out infinite;
}
@keyframes lime-throb{0%,100%{box-shadow:0 10px 30px rgba(101,163,13,.34),0 0 20px rgba(163,230,53,.45),inset 0 0 22px rgba(255,255,255,.5)}
  50%{box-shadow:0 10px 34px rgba(101,163,13,.44),0 0 40px rgba(163,230,53,.95),inset 0 0 26px rgba(255,255,255,.6)}}
.lime-bubble-inner img{max-width:74%;max-height:74%;object-fit:contain;
  filter:drop-shadow(0 2px 6px rgba(40,70,10,.4));animation:lime-spin 14s linear infinite}
@keyframes lime-spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
.lime-bubble-inner::before{content:'';position:absolute;top:11%;left:17%;width:27%;height:18%;
  background:rgba(255,255,255,.92);border-radius:50%!important;filter:blur(1px)}

/* The sword flying out of the popped bubble into the briefing. */
.lime-flyer{position:fixed;z-index:1150;pointer-events:none;width:120px;
  filter:drop-shadow(0 12px 30px rgba(60,110,10,.5));transition:none}

/* Stage. pointer-events:none is load-bearing — it's what keeps the
   sponsor bubbles behind the trial clickable (and dangerous). */
.lime-stage{position:fixed;inset:0;z-index:900;pointer-events:none;
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.lime-stage.dim::before{content:'';position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(120% 80% at 50% 50%,rgba(12,26,4,.30),rgba(8,18,2,.68));
  animation:lime-fade .5s ease both}
@keyframes lime-fade{from{opacity:0}to{opacity:1}}

/* Briefing / results panels — these DO take clicks. */
.lime-panel{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  padding:24px;pointer-events:auto;background:rgba(6,16,2,.82);
  -webkit-backdrop-filter:blur(7px);backdrop-filter:blur(7px);animation:lime-fade .35s ease both}
.lime-card{width:min(560px,100%);max-height:88vh;overflow-y:auto;text-align:center;
  background:linear-gradient(180deg,rgba(24,44,8,.97),rgba(12,26,4,.97));
  border:2px solid rgba(163,230,53,.55);border-radius:20px!important;padding:30px 28px 26px;
  box-shadow:0 30px 90px rgba(0,0,0,.6),0 0 60px rgba(132,204,22,.22);color:#ECFCCB;
  animation:lime-pop .4s cubic-bezier(.2,.9,.3,1.2) both}
@keyframes lime-pop{from{transform:scale(.9) translateY(14px);opacity:0}to{transform:none;opacity:1}}
.lime-card h2{font-size:1.55rem;font-weight:900;letter-spacing:-.02em;margin:0 0 8px;color:#D9F99D}
.lime-card p{font-size:.94rem;line-height:1.6;color:#D9F99D;opacity:.92;margin:0 0 12px}
.lime-card .lime-sub{font-size:.8rem;text-transform:uppercase;letter-spacing:.14em;
  font-weight:800;color:#A3E635;margin-bottom:6px}
.lime-card img.lime-art{width:min(210px,52%);margin:2px auto 14px;display:block;
  filter:drop-shadow(0 14px 34px rgba(60,110,10,.65));animation:lime-hover 3.4s ease-in-out infinite}
@keyframes lime-hover{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-12px) rotate(2deg)}}
.lime-btn{display:inline-block;margin-top:8px;border:0;cursor:pointer;
  background:linear-gradient(135deg,#A3E635,#65A30D);color:#14300A;
  font:900 1rem/1 system-ui,sans-serif;letter-spacing:.02em;
  padding:14px 30px;border-radius:999px!important;
  box-shadow:0 10px 26px rgba(101,163,13,.5)}
.lime-btn:hover{filter:brightness(1.08);transform:translateY(-2px)}
.lime-btn.ghost{background:none;color:#A3E635;border:2px solid rgba(163,230,53,.5);
  box-shadow:none;margin-left:8px;padding:12px 24px}
/* Full-combo dressing on the results and bestowal cards. Pink, because the
   role on the line is osu Champion and nothing else on this page is. */
.lime-fc-banner{display:inline-block;margin:0 0 10px;padding:7px 18px;
  border-radius:999px!important;font:900 .78rem/1 system-ui,sans-serif;
  letter-spacing:.16em;text-transform:uppercase;color:#3B0620;
  background:linear-gradient(135deg,#FFD1E8,#FF66AA);
  box-shadow:0 8px 24px rgba(255,102,170,.45);
  animation:lime-fc-glow 1.8s ease-in-out infinite}
@keyframes lime-fc-glow{0%,100%{box-shadow:0 8px 24px rgba(255,102,170,.4)}
  50%{box-shadow:0 8px 34px rgba(255,102,170,.9)}}
.lime-champ{margin:16px 0 4px;padding:14px 16px;border-radius:14px!important;
  background:rgba(255,102,170,.1);border:1px solid rgba(255,102,170,.45)}
.lime-champ-title{font:900 1.05rem/1 system-ui,sans-serif;color:#FF8FC5;
  letter-spacing:.02em;margin-bottom:8px}
.lime-champ p{margin:0;color:#FFD9EC;font-size:.9rem;line-height:1.55}

.lime-rules{list-style:none;padding:0;margin:14px 0 4px;font-size:.88rem;color:#D9F99D}
.lime-rules li{padding:7px 0;border-top:1px solid rgba(163,230,53,.2)}
.lime-rules li:first-child{border-top:0}

/* HUD */
.lime-hud{position:absolute;top:0;left:0;right:0;padding:14px 20px;pointer-events:none;
  display:flex;align-items:center;justify-content:center;gap:clamp(12px,4vw,44px);
  background:linear-gradient(180deg,rgba(6,16,2,.72),transparent);color:#ECFCCB}
.lime-stat{text-align:center;min-width:74px}
.lime-stat b{display:block;font-size:1.5rem;font-weight:900;line-height:1.1;
  font-variant-numeric:tabular-nums;color:#D9F99D;text-shadow:0 2px 12px rgba(0,0,0,.7)}
.lime-stat span{font-size:.64rem;text-transform:uppercase;letter-spacing:.12em;
  font-weight:800;color:#A3E635;opacity:.9}
.lime-stat.danger b{color:#FCA5A5}
/* The combo stat, which is also the full-combo tracker. Pink while the FC is
   still alive (osu's colour — that's the role on the line), grey once it's
   gone, and it shatters on every streak break. */
.lime-stat.lime-fc.alive b{color:#FF8FC5;text-shadow:0 0 16px rgba(255,102,170,.8)}
.lime-stat.lime-fc.alive span{color:#FF8FC5}
.lime-stat.lime-fc.dead b{color:#A8B79A}
.lime-stat.lime-fc.dead span{color:#8DA07E}
.lime-stat.lime-fc.shatter{animation:lime-shatter .34s ease-out}
@keyframes lime-shatter{
  0%{transform:translateX(0) scale(1.12)}
  25%{transform:translateX(-5px) scale(1.04)}
  55%{transform:translateX(4px) scale(.98)}
  100%{transform:none}}
.lime-fclost{position:absolute;left:50%;top:120px;transform:translateX(-50%);
  pointer-events:none;font:900 .82rem/1 system-ui,sans-serif;letter-spacing:.14em;
  text-transform:uppercase;color:#FFC7E0;background:rgba(80,10,40,.55);
  border:1px solid rgba(255,102,170,.5);padding:8px 16px;border-radius:999px!important;
  animation:lime-fclost 1.4s ease-out both}
@keyframes lime-fclost{
  0%{opacity:0;transform:translateX(-50%) scale(.8)}
  12%{opacity:1;transform:translateX(-50%) scale(1)}
  70%{opacity:1}
  100%{opacity:0;transform:translateX(-50%) translateY(-14px)}}
.lime-bar{position:absolute;left:0;right:0;top:0;height:4px;background:rgba(163,230,53,.18)}
.lime-bar i{display:block;height:100%;background:linear-gradient(90deg,#BEF264,#65A30D);
  width:100%;transform-origin:left center}
.lime-warn{position:absolute;left:50%;top:82px;transform:translateX(-50%);
  pointer-events:none;font-size:.76rem;font-weight:800;letter-spacing:.06em;
  color:#FDE68A;background:rgba(6,16,2,.6);padding:6px 14px;border-radius:999px!important;
  border:1px solid rgba(253,230,138,.35)}

/* A lime in play. This is the only thing in the stage that takes a click. */
.lime-target{position:absolute;pointer-events:auto;cursor:pointer;background:none;border:0;
  padding:0;margin:0;-webkit-appearance:none;appearance:none;outline:none;
  width:var(--s,78px);height:var(--s,78px);will-change:transform,opacity;
  animation:lime-life var(--life,1500ms) linear both}
.lime-target img{width:100%;height:100%;object-fit:contain;pointer-events:none;
  filter:drop-shadow(0 6px 16px rgba(20,50,0,.65)) drop-shadow(0 0 12px rgba(190,242,100,.5))}
@keyframes lime-life{
  0%{opacity:0;transform:scale(.5) rotate(var(--r0,-14deg))}
  12%{opacity:1;transform:scale(1.06) rotate(0deg)}
  20%{transform:scale(1) rotate(0deg)}
  78%{opacity:1;transform:scale(1) rotate(0deg)}
  100%{opacity:0;transform:scale(.72) rotate(var(--r1,10deg))}}

/* The cut itself. The wrapper is rotated to the slice angle, so everything
   inside can be written along a flat horizontal axis: a blade streak sweeps
   across the middle, and the two halves fall away perpendicular to it. */
.lime-cut{position:absolute;pointer-events:none;transform:rotate(var(--a,0deg));
  will-change:transform}
.lime-cut .lime-half{position:absolute;left:0;width:100%;height:50%;overflow:hidden}
.lime-cut .lime-half img{position:absolute;left:0;width:100%;height:200%;
  object-fit:contain;
  filter:drop-shadow(0 6px 16px rgba(20,50,0,.65)) drop-shadow(0 0 12px rgba(190,242,100,.5))}
.lime-cut .lime-half.top{top:0;animation:lime-half-a .55s cubic-bezier(.2,.75,.3,1) both}
.lime-cut .lime-half.top img{top:0}
.lime-cut .lime-half.bot{top:50%;animation:lime-half-b .55s cubic-bezier(.2,.75,.3,1) both}
.lime-cut .lime-half.bot img{top:-100%}
@keyframes lime-half-a{
  0%{transform:none;opacity:1}
  100%{transform:translate(-10px,-40px) rotate(-26deg);opacity:0}}
@keyframes lime-half-b{
  0%{transform:none;opacity:1}
  100%{transform:translate(10px,40px) rotate(22deg);opacity:0}}
/* The blade streak, drawn edge to edge through the cut line. */
.lime-cut .lime-slash{position:absolute;left:-30%;width:160%;height:5px;
  top:calc(50% - 2.5px);border-radius:999px!important;transform-origin:left center;
  background:linear-gradient(90deg,rgba(255,255,255,0),rgba(255,255,255,.98) 35%,
             rgba(217,249,157,.95) 65%,rgba(190,242,100,0));
  filter:drop-shadow(0 0 10px rgba(190,242,100,.95));
  animation:lime-slash .42s cubic-bezier(.15,.85,.25,1) both}
@keyframes lime-slash{
  0%{transform:scaleX(0);opacity:0}
  22%{transform:scaleX(1);opacity:1}
  100%{transform:scaleX(1);opacity:0}}
/* Juice thrown off the blade, flung along the slice. */
.lime-drop{position:absolute;pointer-events:none;border-radius:50%!important;
  background:radial-gradient(circle at 35% 30%,#F7FEE7,#A3E635 60%,#65A30D);
  box-shadow:0 0 8px rgba(163,230,53,.8);
  animation:lime-drop .5s ease-out both}
@keyframes lime-drop{
  0%{transform:translate(0,0) scale(1);opacity:1}
  100%{transform:translate(var(--dx,0),var(--dy,0)) scale(.3);opacity:0}}

/* Score pops and the burst left behind by a cut lime. */
.lime-pop{position:absolute;pointer-events:none;font:900 1.05rem/1 system-ui,sans-serif;
  color:#ECFCCB;text-shadow:0 2px 10px rgba(20,50,0,.9);animation:lime-float .7s ease-out both}
.lime-pop.great{color:#FDE68A}
@keyframes lime-float{from{transform:translateY(0);opacity:1}to{transform:translateY(-46px);opacity:0}}
.lime-burst{position:absolute;pointer-events:none;border-radius:50%!important;
  border:3px solid rgba(190,242,100,.9);animation:lime-ring .45s ease-out both}
@keyframes lime-ring{from{transform:scale(.4);opacity:.95}to{transform:scale(1.9);opacity:0}}

@media (max-width:560px){
  .lime-card{padding:24px 18px}
  .lime-hud{gap:10px;padding:10px 8px}
  .lime-stat b{font-size:1.15rem}
  .lime-stat{min-width:56px}
}`;
    document.head.appendChild(el);
  }

  /* ── Small helpers ──────────────────────────────────────────── */
  const rnd = (a, b) => a + Math.random() * (b - a);

  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  /* The stage sits under the fixed sidenav (z-index 1000 vs the stage's 900),
     so anything spawned in that strip is buried and uncuttable. Off-canvas on
     mobile, where the rect lands at or left of zero. */
  function navRight() {
    const nav = document.querySelector('.sidenav');
    if (!nav) return 0;
    const r = nav.getBoundingClientRect();
    return Math.max(0, Math.min(r.right, window.innerWidth * 0.4));
  }

  /* Spawn offsets for all TOTAL limes, in ms from the start of the run. */
  function schedule() {
    const out = [];
    let t = 0;
    for (const p of PHASES) {
      const gap = 1000 / p.cps;
      for (let i = 0; i < p.count; i++) { out.push(t); t += gap; }
    }
    return out;
  }

  /* ── The trial ──────────────────────────────────────────────── */
  function LimeTrial(onDone) {
    const root = el('div', 'lime-stage');
    document.body.appendChild(root);

    let audio = null;
    let stopTracking = null;   // set for the length of a run — see run()
    try {
      audio = new Audio(MUSIC);
      audio.loop = true;
      audio.volume = 0.55;
    } catch (_) { audio = null; }

    function stopMusic() {
      if (!audio) return;
      try { audio.pause(); audio.currentTime = 0; } catch (_) {}
    }

    function clear() { root.innerHTML = ''; root.classList.remove('dim'); }

    function close() {
      stopMusic();
      if (stopTracking) { stopTracking(); stopTracking = null; }
      root.remove();
      document.removeEventListener('keydown', onKey, true);
      if (onDone) onDone();
    }

    function onKey(e) {
      // Esc only backs out of a panel — never mid-run, where it would read
      // as "I quit" on a keypress meant for something else.
      if (e.key !== 'Escape') return;
      if (root.querySelector('.lime-panel')) { e.preventDefault(); close(); }
    }
    document.addEventListener('keydown', onKey, true);

    /* ── 1. The briefing ── */
    function briefing() {
      clear();
      const panel = el('div', 'lime-panel');
      panel.appendChild(el('div', 'lime-card', `
        <img class="lime-art" src="${REFORGED ? IMG_WHOLE : IMG_BROKEN}"
             alt="${REFORGED ? 'The Lime Sword' : 'The broken Lime Sword'}" />
        <div class="lime-sub">Calamity Catalyst</div>
        <h2>${REFORGED ? 'The Lime Sword remembers' : 'The Lime Sword lies broken'}</h2>
        <p>${REFORGED
          ? 'You\'ve already reforged it once. The limes still fall for anyone willing to swing — go chase a better score.'
          : 'Someone shattered the blade and scattered it into citrus. The pieces are still falling — and they don\'t wait to be picked up.'}</p>
        <p>Cut them out of the air before they rot away and the sword reforges
        itself in your hand.</p>
        <ul class="lime-rules">
          <li><strong>${TOTAL} limes</strong> will appear, fast. Cut
              <strong>${Math.round(PASS_PCT * 100)}%</strong> of them —
              that's ${TARGET}.</li>
          <li>You are <em>not</em> meant to get them all. Half is the blade's price.</li>
          <li>🎯 Cut <strong>every single one</strong> and it's a full combo —
              <strong>+${FC_POINTS.toLocaleString()} points</strong> and the
              <strong>osu Champion</strong> role, once, ever.</li>
          <li>The sooner you cut one, the more it's worth — <strong>${BEST_HIT}</strong>
              down to <strong>${WORST_HIT}</strong> as it fades.</li>
          <li>They fall within reach of your cursor — and come faster the
              longer you last.</li>
          <li>⚠️ The sponsor bubbles are still live. Hit one and its package
              lands right on top of you — the clock won't wait.</li>
        </ul>
        <button class="lime-btn" type="button" data-go>Take the trial →</button>
        <button class="lime-btn ghost" type="button" data-quit>Not yet</button>`));
      root.appendChild(panel);
      panel.querySelector('[data-go]').addEventListener('click', run);
      panel.querySelector('[data-quit]').addEventListener('click', close);
    }

    /* ── 2. The run ── */
    function run() {
      clear();
      root.classList.add('dim');
      if (audio) {
        try {
          audio.currentTime = 0;
          // Older browsers return undefined rather than a promise here.
          const p = audio.play();
          if (p && p.catch) p.catch(() => {});
        } catch (_) { /* autoplay refused — the trial runs silent */ }
      }

      const hud = el('div', 'lime-hud', `
        <div class="lime-stat"><b data-time>60.0</b><span>seconds</span></div>
        <div class="lime-stat"><b data-hits>0</b><span>of ${TARGET} cut</span></div>
        <div class="lime-stat lime-fc"><b data-combo>0</b><span data-combolab>combo</span></div>
        <div class="lime-stat"><b data-score>0</b><span>score</span></div>
        <div class="lime-stat"><b data-left>${TOTAL}</b><span>limes left</span></div>`);
      const bar = el('div', 'lime-bar', '<i></i>');
      root.appendChild(bar);
      root.appendChild(hud);
      root.appendChild(el('div', 'lime-warn', 'Don\'t touch the sponsor bubbles.'));

      const elTime  = hud.querySelector('[data-time]');
      const elHits  = hud.querySelector('[data-hits]');
      const elScore = hud.querySelector('[data-score]');
      const elLeft  = hud.querySelector('[data-left]');
      const elCombo = hud.querySelector('[data-combo]');
      const elComboLab = hud.querySelector('[data-combolab]');
      const elBar   = bar.querySelector('i');
      const hitsBox = elHits.parentElement;
      const comboBox = elCombo.parentElement;

      const times = schedule();
      const live = new Set();
      let next = 0, hits = 0, score = 0, best = 0, sumMs = 0, done = false;
      // combo is the current unbroken streak; maxCombo is the run's best.
      // A full combo means nothing was ever missed — hits === TOTAL.
      let combo = 0, maxCombo = 0, missed = 0;
      const t0 = performance.now();

      /* Where the limes spawn around. Starts at the middle of the playfield
         until the pointer says otherwise — a touch player never moves it, and
         the centre is the fairest guess for them. */
      let mouseX = (navRight() + window.innerWidth) / 2;
      let mouseY = window.innerHeight / 2;
      function track(e) { mouseX = e.clientX; mouseY = e.clientY; }
      window.addEventListener('pointermove', track, { passive: true });
      window.addEventListener('pointerdown', track, { passive: true });
      function untrack() {
        window.removeEventListener('pointermove', track);
        window.removeEventListener('pointerdown', track);
      }
      stopTracking = untrack;

      function place(size) {
        // Keep clear of the HUD up top, the sidenav down the left, and the
        // screen edges — then land inside a ring around the cursor so the
        // next lime is always a short flick away, not a sprint. Also try not
        // to drop a lime straight on top of one that's already out.
        const pad = 14;
        const minX = navRight() + pad;
        const maxX = Math.max(minX, window.innerWidth  - size - pad);
        const minY = 104, maxY = Math.max(minY, window.innerHeight - size - pad);

        // Shrink the reach on small windows so the ring still fits on screen.
        const span = Math.min(maxX - minX, maxY - minY);
        const far  = Math.max(REACH_MIN + 90, Math.min(REACH_MAX, span * 0.55));
        const near = Math.min(REACH_MIN + size * 0.5, far - 40);

        let x = (minX + maxX) / 2, y = (minY + maxY) / 2;
        let placed = false;
        for (let tries = 0; tries < 26 && !placed; tries++) {
          const a = rnd(0, Math.PI * 2), r = rnd(near, far);
          const cx = mouseX + Math.cos(a) * r, cy = mouseY + Math.sin(a) * r;
          const nx = cx - size / 2, ny = cy - size / 2;
          if (nx < minX || nx > maxX || ny < minY || ny > maxY) continue;
          let clash = false;
          for (const n of live) {
            if (Math.hypot(n._x - nx, n._y - ny) < size * 0.94) { clash = true; break; }
          }
          // Last few tries, take anything on screen rather than fall through
          // to the clamp — an overlap beats a lime pinned to the cursor.
          if (clash && tries < 20) continue;
          x = nx; y = ny; placed = true;
        }
        if (!placed) {
          // Cursor jammed into a corner with no room in the ring: clamp the
          // last candidate back on screen.
          x = Math.min(maxX, Math.max(minX, mouseX - size / 2 + rnd(-far, far)));
          y = Math.min(maxY, Math.max(minY, mouseY - size / 2 + rnd(-far, far)));
        }
        return { x, y };
      }

      function spawn() {
        const size = Math.round(rnd(64, 92) * (window.innerWidth < 620 ? 0.82 : 1));
        const { x, y } = place(size);
        const node = el('button', 'lime-target',
          `<img src="${IMG_LIME}" alt="" draggable="false" />`);
        node.type = 'button';
        node.setAttribute('aria-label', 'Cut the lime');
        node.style.setProperty('--s', size + 'px');
        node.style.setProperty('--life', LIFE_MS + 'ms');
        node.style.setProperty('--r0', rnd(-26, 26).toFixed(0) + 'deg');
        node.style.setProperty('--r1', rnd(-22, 22).toFixed(0) + 'deg');
        node.style.left = x + 'px';
        node.style.top  = y + 'px';
        node._x = x; node._y = y; node._born = performance.now();
        node.addEventListener('pointerdown', ev => {
          ev.preventDefault();
          ev.stopPropagation();
          cut(node);
        });
        live.add(node);
        root.appendChild(node);
      }

      function cut(node) {
        if (!live.has(node)) return;
        live.delete(node);
        const age = Math.min(LIFE_MS, Math.max(0, performance.now() - node._born));
        const worth = Math.round(BEST_HIT - (BEST_HIT - WORST_HIT) * (age / LIFE_MS));
        hits += 1; score += worth; sumMs += age;
        combo += 1;
        if (combo > maxCombo) maxCombo = combo;
        if (worth > best) best = worth;

        const size = parseFloat(node.style.getPropertyValue('--s')) || 78;

        // The slice. The blade comes in along a random angle, the lime falls
        // open into two halves either side of it, and a little juice flies
        // off down the same line.
        const ang = rnd(0, 180);
        const rad = ang * Math.PI / 180;
        const slice = el('div', 'lime-cut', `
          <div class="lime-half top"><img src="${IMG_LIME}" alt="" draggable="false" /></div>
          <div class="lime-half bot"><img src="${IMG_LIME}" alt="" draggable="false" /></div>
          <div class="lime-slash"></div>`);
        slice.style.left = node._x + 'px';
        slice.style.top  = node._y + 'px';
        slice.style.width = size + 'px';
        slice.style.height = size + 'px';
        slice.style.setProperty('--a', ang.toFixed(1) + 'deg');
        root.appendChild(slice);
        setTimeout(() => slice.remove(), 620);

        for (let i = 0; i < 6; i++) {
          const d = el('div', 'lime-drop');
          const w = rnd(5, 11);
          // Thrown along the cut, either direction, with a bit of scatter.
          const spread = rnd(-0.5, 0.5) + (i % 2 ? 0 : Math.PI);
          const dist = rnd(30, 78);
          d.style.width = d.style.height = w.toFixed(1) + 'px';
          d.style.left = (node._x + size / 2 - w / 2) + 'px';
          d.style.top  = (node._y + size / 2 - w / 2) + 'px';
          d.style.setProperty('--dx', (Math.cos(rad + spread) * dist).toFixed(1) + 'px');
          d.style.setProperty('--dy', (Math.sin(rad + spread) * dist).toFixed(1) + 'px');
          d.style.animationDelay = (i * 12) + 'ms';
          root.appendChild(d);
          setTimeout(() => d.remove(), 600);
        }

        const burst = el('div', 'lime-burst');
        burst.style.left = node._x + 'px';
        burst.style.top  = node._y + 'px';
        burst.style.width = size + 'px';
        burst.style.height = size + 'px';
        root.appendChild(burst);
        setTimeout(() => burst.remove(), 460);

        const pop = el('div', 'lime-pop' + (worth >= 900 ? ' great' : ''), '+' + worth);
        pop.style.left = (node._x + size * 0.5 - 22) + 'px';
        pop.style.top  = (node._y + size * 0.2) + 'px';
        root.appendChild(pop);
        setTimeout(() => pop.remove(), 720);

        node.remove();
        paint();
      }

      /* Restart the shatter animation on the combo stat — removing the class
         and forcing a reflow is what makes it replay on a back-to-back break. */
      function breakCombo() {
        comboBox.classList.remove('shatter');
        void comboBox.offsetWidth;
        comboBox.classList.add('shatter');
      }

      /* Said once, on the miss that ends the full combo, so nobody has to
         work out why the counter went grey. */
      function fcLost() {
        const note = el('div', 'lime-fclost', 'Full combo lost');
        root.appendChild(note);
        setTimeout(() => note.remove(), 1400);
      }

      function paint() {
        elHits.textContent = hits;
        elScore.textContent = score.toLocaleString();
        elLeft.textContent = Math.max(0, TOTAL - next);
        elCombo.textContent = combo;
        // The combo readout doubles as the full-combo tracker: it stays lit
        // while nothing has been missed, and goes dead the moment one is.
        elComboLab.textContent = missed ? 'combo · best ' + maxCombo : 'combo · FC alive';
        comboBox.classList.toggle('alive', !missed);
        comboBox.classList.toggle('dead', !!missed);
        // Once more limes have been missed than the run can afford, the
        // pass is already out of reach — turn the counter red.
        hitsBox.classList.toggle('danger', missed > TOTAL - TARGET);
      }

      function frame(now) {
        if (done) return;
        const t = now - t0;

        while (next < TOTAL && times[next] <= t) { spawn(); next += 1; }

        for (const n of Array.from(live)) {
          if (now - n._born >= LIFE_MS) {
            live.delete(n); n.remove();
            missed += 1;
            if (missed === 1) fcLost();
            if (combo) breakCombo();
            combo = 0;
            paint();
          }
        }

        const remain = Math.max(0, LIMIT_MS - t);
        elTime.textContent = (remain / 1000).toFixed(1);
        elBar.style.transform = 'scaleX(' + (remain / LIMIT_MS).toFixed(4) + ')';
        elTime.parentElement.classList.toggle('danger', remain < 10000);
        elLeft.textContent = Math.max(0, TOTAL - next);

        // Two ways to finish: every lime has come and gone, or the minute
        // ran out. The ramp lands the last lime around 40s, so the first
        // is what normally ends it.
        if ((next >= TOTAL && live.size === 0) || t >= LIMIT_MS) {
          done = true;
          stopMusic();
          untrack();
          stopTracking = null;
          setTimeout(() => results({
            hits, score,
            avgMs: hits ? Math.round(sumMs / hits) : 0,
            best, maxCombo,
            fullCombo: hits >= TOTAL,
          }), 420);
          return;
        }
        requestAnimationFrame(frame);
      }
      paint();
      requestAnimationFrame(frame);
    }

    /* ── 3. Results ── */
    function results(r) {
      clear();
      const passed = r.hits >= TARGET;
      const acc = Math.round((r.hits / TOTAL) * 100);
      const panel = el('div', 'lime-panel');
      panel.appendChild(el('div', 'lime-card', passed ? `
        ${r.fullCombo ? '<div class="lime-fc-banner">🎯 Full combo</div>' : ''}
        <div class="lime-sub">Trial cleared</div>
        <h2>${r.fullCombo ? 'Not one lime touched the ground' : 'The blade answers 🍋'}</h2>
        <p>${r.fullCombo
          ? `All ${TOTAL} of them, in a single minute. Claim the blade and the
             bounty comes with it.`
          : `${r.hits} of ${TOTAL} limes cut. The pieces are moving on their own now.`}</p>
        <ul class="lime-rules">
          <li>Score <strong>${r.score.toLocaleString()}</strong></li>
          <li>Limes cut <strong>${r.hits} / ${TOTAL}</strong> · ${acc}%</li>
          <li>Best combo <strong>${r.maxCombo}</strong>${r.fullCombo ? ' — unbroken' : ''}</li>
          <li>Average reaction <strong>${r.avgMs} ms</strong></li>
          <li>Best single cut <strong>${r.best}</strong></li>
        </ul>
        <button class="lime-btn" type="button" data-claim>Reforge the sword →</button>` : `
        <div class="lime-sub">Trial failed</div>
        <h2>Not enough, ${r.hits < TARGET - 20 ? 'not nearly' : 'so close'}</h2>
        <p>You cut <strong>${r.hits}</strong> of ${TOTAL}. The blade needs
        <strong>${TARGET}</strong> — ${TARGET - r.hits} more.</p>
        <ul class="lime-rules">
          <li>Score <strong>${r.score.toLocaleString()}</strong></li>
          <li>Accuracy <strong>${acc}%</strong></li>
          <li>Best combo <strong>${r.maxCombo}</strong></li>
          <li>Average reaction <strong>${r.avgMs || '—'} ms</strong></li>
        </ul>
        <p>The limes are still falling. Go again.</p>
        <button class="lime-btn" type="button" data-retry>Try again →</button>
        <button class="lime-btn ghost" type="button" data-quit>Walk away</button>`));
      root.appendChild(panel);

      if (passed) {
        panel.querySelector('[data-claim]').addEventListener('click', () => claim(r, panel));
      } else {
        panel.querySelector('[data-retry]').addEventListener('click', run);
        panel.querySelector('[data-quit]').addEventListener('click', close);
      }
    }

    /* ── 4. The bestowal ── */
    async function claim(r, panel) {
      const btn = panel.querySelector('[data-claim]');
      btn.disabled = true;
      btn.textContent = 'Reforging…';
      let err = null;
      let got = null;    // what the server actually granted
      try {
        const res = await fetch('/api/students/me/claim-lime-sword', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hits: r.hits, score: r.score }),
        });
        const j = await res.json().catch(() => null);
        if (!res.ok || !j || !j.ok) err = (j && j.error) || 'The forge went cold. Try claiming again.';
        else got = j.data || {};
      } catch (_) {
        err = 'Could not reach the forge — check your connection and claim again.';
      }
      if (err) {
        btn.disabled = false;
        btn.textContent = 'Reforge the sword →';
        let warn = panel.querySelector('.lime-claim-err');
        if (!warn) {
          warn = el('p', 'lime-claim-err');
          warn.style.cssText = 'color:#FCA5A5;font-weight:700;margin-top:12px';
          panel.querySelector('.lime-card').appendChild(warn);
        }
        warn.textContent = err;
        return;
      }

      // Refresh the cached student so the portal's roles panel has it.
      try {
        if (window.HG && window.HG.refresh) await window.HG.refresh();
      } catch (_) {}

      // The server decides what a full combo is worth — the bounty is
      // one-time, so a second perfect run says so instead of promising
      // points that were already paid.
      const fc = got && got.fullCombo;
      const paid = (got && got.pointsAwarded) || 0;
      const champBlock = !fc ? '' : `
        <div class="lime-champ">
          <div class="lime-champ-title">🎯 osu Champion</div>
          <p>${paid
            ? `Full combo. All ${TOTAL} limes, nothing missed — the role is on
               your profile and <strong>+${paid.toLocaleString()} points</strong>
               are in your balance.`
            : `Full combo again. The role is already yours and the
               ${FC_POINTS.toLocaleString()}-point bounty only pays once —
               this one was for the record.`}</p>
        </div>`;

      clear();
      const done = el('div', 'lime-panel');
      done.appendChild(el('div', 'lime-card', `
        <img class="lime-art" src="${IMG_WHOLE}" alt="The Lime Sword, whole again"
             style="width:min(240px,60%)" />
        <div class="lime-sub">The Lime Sword is yours</div>
        <h2>Whole again</h2>
        <p>${r.score.toLocaleString()} points, ${r.hits} limes, one blade.
        ${REFORGED ? 'It was already yours — consider that a sharper edge.'
                   : 'It\'s on your camper profile now.'}</p>
        ${champBlock}
        <p style="font-size:1.12rem;color:#ECFCCB;margin-top:16px">
          Time to go on a spider <strong>hunt</strong>.</p>
        <button class="lime-btn" type="button" data-close>Done</button>`));
      root.appendChild(done);
      done.querySelector('[data-close]').addEventListener('click', close);
    }

    briefing();
  }

  /* ── The bubble that starts it all ──────────────────────────── */
  function addBubble() {
    if (document.querySelector('.lime-bubble')) return;
    const b = el('button', 'lime-bubble',
      `<span class="lime-bubble-inner"><img src="${IMG_LIME}" alt="" /></span>`);
    b.type = 'button';
    b.setAttribute('aria-label', 'A lime bubble — the Calamity Catalyst trial');
    b.title = 'Something is broken in here…';
    const size = Math.round(rnd(96, 124));
    // Same sidenav problem as the limes — it draws over the bubble, so a
    // bubble that drifts up the left strip is invisible and unclickable.
    const lo = navRight() + 60;   // + the drift the rise animation adds
    b.style.setProperty('--lx', Math.round(rnd(lo, Math.max(lo, window.innerWidth - size - 60))) + 'px');
    b.style.setProperty('--lsize', size + 'px');
    b.style.setProperty('--ldur', rnd(30, 40).toFixed(1) + 's');
    b.style.setProperty('--ldelay', (-rnd(2, 14)).toFixed(1) + 's');
    b.style.setProperty('--ldrift', rnd(-40, 40).toFixed(0) + 'px');

    b.addEventListener('click', () => {
      if (document.querySelector('.lime-stage')) return;
      const r = b.getBoundingClientRect();
      b.style.display = 'none';

      // The broken sword floats up out of the bubble you clicked, and the
      // briefing opens behind it as it lands.
      const fly = el('img', 'lime-flyer');
      fly.src = IMG_BROKEN;
      fly.alt = '';
      fly.style.left = (r.left + r.width / 2 - 60) + 'px';
      fly.style.top  = (r.top + r.height / 2 - 60) + 'px';
      fly.style.opacity = '0';
      fly.style.transform = 'scale(.35) rotate(-24deg)';
      document.body.appendChild(fly);
      requestAnimationFrame(() => {
        fly.style.transition = 'transform 1s cubic-bezier(.2,.8,.3,1), opacity .9s ease, top 1s cubic-bezier(.2,.8,.3,1), left 1s cubic-bezier(.2,.8,.3,1)';
        fly.style.opacity = '1';
        fly.style.left = (window.innerWidth / 2 - 60) + 'px';
        fly.style.top  = (window.innerHeight * 0.5 - 150) + 'px';
        fly.style.transform = 'scale(1.5) rotate(6deg)';
      });
      setTimeout(() => {
        fly.style.transition = 'opacity .4s ease';
        fly.style.opacity = '0';
        setTimeout(() => fly.remove(), 420);
        // Marcus gets to react to the state of the blade before the briefing
        // opens — the "it's shattered" line lands better once they've seen
        // it. He always hands control back; if he isn't on the page at all,
        // the trial just starts.
        const start = () => LimeTrial(() => { b.style.display = ''; });
        if (window.HGMarcus && window.HGMarcus.bubbleOpened) {
          window.HGMarcus.bubbleOpened(start);
        } else {
          start();
        }
      }, 1050);
    });

    document.body.appendChild(b);
  }

  /* Role ids that count as `name`. Staff often create a role by hand before
     the code that uses it ships, and the roles table keys NAME as unique —
     so the seeded id can lose the race and never exist. Matching on the
     name as well means whichever row actually exists is the one that
     counts. Mirrors _role_ids_named() on the server. */
  function idsNamed(name, seededId) {
    const want = String(name).toLowerCase().replace(/\s+/g, '');
    const out = new Set([seededId]);
    const all = (window.HG && window.HG.cache && window.HG.cache.roles) || [];
    for (const r of all) {
      if (String(r && r.name || '').toLowerCase().replace(/\s+/g, '') === want) out.add(r.id);
    }
    return out;
  }
  const holds = (roles, ids) => roles.some(id => ids.has(id));

  /* ── Boot ───────────────────────────────────────────────────── */
  (async function boot() {
    if (window.dataReady) { try { await window.dataReady; } catch (_) {} }
    const me = window.HG && window.HG.cache && window.HG.cache.me
      ? window.HG.cache.me.student : null;
    if (!me) return;                                    // signed out — no bubble
    const roles = Array.isArray(me.roles) ? me.roles : [];
    if (!holds(roles, idsNamed('Calamity Catalyst', CATALYST_ROLE))) return;
    // Already reforged it? The bubble stays — the trial is replayable for a
    // better score, and claiming again is a no-op server-side.
    REFORGED = holds(roles, idsNamed('Lime Sword', SWORD_ROLE));
    injectStyles();
    addBubble();
  })();
})();
