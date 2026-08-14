/* ============================================================
   DUNGEON — shop, inventory, receipts and the tax return.
   ----------------------------------------------------------------
   Mounts itself into #dungeon-panel on the student portal. Four
   tabs: Shop, Inventory (equipment slots + everything owned),
   Receipts, and Tax.

   The tax tab is the point of the whole feature: 13% is withheld
   on every earn and every spend, each one writes a receipt, and
   the only way to get it back is to add the receipts up yourself
   and file the right number.
   ============================================================ */
(function () {
  'use strict';

  const $ = s => document.querySelector(s);
  const fmt = n => Number(n || 0).toLocaleString();
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const gem = (n) => `<span class="dg-gem">${window.DungeonIcons.shard(13)}${fmt(n)}</span>`;
  /* Note text is authored server-side with **stars** around the word that
     matters. Escape first, then promote the stars — so the emphasis is the
     only markup that ever survives into the page. */
  const bold = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  const SLOT_LABEL = {
    helmet: 'Helmet', chestplate: 'Chestplate', leggings: 'Leggings', boots: 'Boots',
    weapon: 'Weapon', back: 'Back', necklace: 'Necklace', amulet: 'Amulet',
    ring: 'Ring', earring: 'Earring',
  };

  let cat = null, me = null, tab = 'shop', busy = false;
  let shopTier = 'all', shopSlot = 'all';
  let recFilter = 'all', recFrom = '', recTo = '';

  async function api(path, body) {
    const r = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body || {}),
    });
    let j = null;
    try { j = await r.json(); } catch (_) {}
    return { ok: r.ok && j && j.ok, data: (j && j.data) || null,
             error: (j && j.error) || 'Something went wrong.', status: r.status };
  }

  function styles() {
    if (document.getElementById('dg-style')) return;
    const el = document.createElement('style');
    el.id = 'dg-style';
    el.textContent = `
.dg{--dg-line:var(--border,#e3e6ea);--dg-dim:var(--muted,#6b7280)}
.dg-gem{display:inline-flex;align-items:center;gap:4px;font-variant-numeric:tabular-nums;
  font-weight:800;white-space:nowrap}
.dg-gem svg{vertical-align:-2px}
.dg-bal{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;
  padding:14px 16px;border:1px solid var(--dg-line);border-radius:14px;
  background:linear-gradient(135deg,rgba(125,211,252,.10),rgba(163,230,53,.08));margin-bottom:14px}
.dg-bal .big{font-size:1.5rem}
.dg-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.dg-tab{border:1px solid var(--dg-line);background:var(--surface,#fff);border-radius:99px;
  padding:8px 16px;font:800 .82rem system-ui,sans-serif;cursor:pointer;color:var(--text,#111)}
.dg-tab.on{background:var(--navy,#16304d);color:#fff;border-color:transparent}
.dg-tab .pip{display:inline-block;min-width:18px;padding:0 5px;margin-left:6px;border-radius:99px;
  background:#DC2626;color:#fff;font-size:.68rem;line-height:18px;text-align:center}
.dg-filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.dg-chip{border:1px solid var(--dg-line);background:var(--surface,#fff);border-radius:99px;
  padding:5px 12px;font:700 .74rem system-ui,sans-serif;cursor:pointer;color:var(--dg-dim)}
.dg-chip.on{background:var(--blush,#E8808A);color:#fff;border-color:transparent}
.dg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.dg-card{border:1px solid var(--dg-line);border-radius:14px;padding:14px;background:var(--surface,#fff);
  display:flex;flex-direction:column;gap:8px}
.dg-card.locked{opacity:.55}
.dg-card .top{display:flex;gap:10px;align-items:flex-start}
.dg-card .nm{font-weight:800;font-size:.92rem;line-height:1.25}
.dg-card .bl{font-size:.76rem;color:var(--dg-dim);line-height:1.5;flex:1}
.dg-tagrow{display:flex;gap:5px;flex-wrap:wrap}
.dg-tag{font-size:.66rem;font-weight:800;padding:2px 7px;border-radius:99px;
  background:rgba(125,211,252,.18);color:#0b5f80}
.dg-tag.tier{background:rgba(163,230,53,.2);color:#3f6212}
.dg-tag.own{background:rgba(148,163,184,.22);color:#334155}
.dg-price{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:auto}
.dg-was{text-decoration:line-through;color:var(--dg-dim);font-size:.76rem;margin-right:5px}
.dg-save{font-size:.68rem;font-weight:800;color:#15803d}
.dg-btn{border:0;border-radius:99px;padding:8px 15px;font:800 .78rem system-ui,sans-serif;
  cursor:pointer;background:var(--navy,#16304d);color:#fff;white-space:nowrap}
.dg-btn.ghost{background:none;border:1px solid var(--dg-line);color:var(--text,#111)}
.dg-btn:disabled{opacity:.45;cursor:default}
.dg-slots{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:10px;margin-bottom:16px}
.dg-slot{border:1.5px dashed var(--dg-line);border-radius:12px;padding:10px 8px;text-align:center;
  background:var(--surface2,#f7f8fa);min-height:104px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:4px}
.dg-slot.filled{border-style:solid;border-color:var(--blush,#E8808A);background:var(--surface,#fff)}
.dg-slot .sl{font-size:.62rem;text-transform:uppercase;letter-spacing:.09em;font-weight:800;color:var(--dg-dim)}
.dg-slot .in{font-size:.74rem;font-weight:700;line-height:1.3}
.dg-slot button{margin-top:3px;font-size:.66rem;background:none;border:0;color:#B91C1C;cursor:pointer;font-weight:800}
.dg-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:8px;margin-bottom:16px}
.dg-stat{border:1px solid var(--dg-line);border-radius:11px;padding:9px;text-align:center;background:var(--surface,#fff)}
.dg-stat .k{font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:var(--dg-dim);font-weight:800}
.dg-stat .v{font-size:1.05rem;font-weight:900;font-variant-numeric:tabular-nums}
.dg-table{width:100%;border-collapse:collapse;font-size:.82rem}
.dg-table th{text-align:left;font-size:.64rem;text-transform:uppercase;letter-spacing:.08em;
  color:var(--dg-dim);padding:7px 6px;border-bottom:1.5px solid var(--dg-line)}
.dg-table td{padding:8px 6px;border-bottom:1px solid var(--dg-line);vertical-align:top}
.dg-table td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.dg-table tr.done{opacity:.5}
.dg-empty{text-align:center;color:var(--dg-dim);font-style:italic;padding:22px 6px;font-size:.86rem}
.dg-note{font-size:.8rem;color:var(--dg-dim);line-height:1.6}
.dg-box{border:1px solid var(--dg-line);border-radius:14px;padding:16px;background:var(--surface,#fff);margin-bottom:14px}
.dg-box h4{margin:0 0 6px;font-size:.95rem}
.dg-in{padding:9px 12px;border:1.5px solid var(--dg-line);border-radius:9px;font-size:.92rem;
  font-variant-numeric:tabular-nums;width:100%;background:var(--surface,#fff);color:var(--text,#111)}
.dg-msg{font-size:.84rem;font-weight:700;margin-top:9px;line-height:1.5}
.dg-msg.ok{color:#15803d} .dg-msg.err{color:#B91C1C} .dg-msg.info{color:#0b5f80}
.dg-help{background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.4)}
.dg-help details{font-size:.85rem;line-height:1.65}
.dg-help summary{cursor:pointer;font-weight:800;font-size:.9rem}
.dg-help h5{margin:14px 0 4px;font-size:.86rem}
.dg-help table{border-collapse:collapse;margin:8px 0;font-size:.82rem}
.dg-help td{padding:3px 10px 3px 0}
/* Quest items — earned, unsellable, and visibly not shop stock. */
.dg-tag.quest{background:rgba(163,230,53,.25);color:#3f6212}
.dg-card.quest{border-color:rgba(163,230,53,.65);
  background:linear-gradient(160deg,rgba(163,230,53,.09),var(--surface,#fff) 62%)}
/* The note itself, opened out of the bag. Deliberately paper rather than a
   dialog box: it's a thing they were handed, not a system message. */
.dg-noteveil{position:fixed;inset:0;z-index:4000;display:flex;align-items:center;
  justify-content:center;padding:22px;background:rgba(10,16,6,.62);
  -webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px);
  animation:dg-note-in .22s ease both}
@keyframes dg-note-in{from{opacity:0}to{opacity:1}}
.dg-notepaper{width:min(430px,100%);text-align:center;padding:30px 26px 24px;
  border-radius:6px;color:#3a3223;
  background:linear-gradient(170deg,#FBF6E4,#F1E7C8);
  border:1px solid rgba(120,100,50,.35);
  box-shadow:0 26px 60px rgba(0,0,0,.45);
  animation:dg-note-pop .3s cubic-bezier(.2,.9,.3,1.25) both}
@keyframes dg-note-pop{from{transform:scale(.92) rotate(-1.5deg);opacity:0}
  to{transform:rotate(-.6deg);opacity:1}}
.dg-notehead{font-size:.66rem;text-transform:uppercase;letter-spacing:.16em;
  font-weight:800;color:#8a7a4e;margin-bottom:14px}
.dg-notebody{font:600 1.28rem/1.55 Georgia,"Times New Roman",serif;margin:0 0 20px}
.dg-notebody strong{font-weight:900;text-decoration:underline;text-underline-offset:4px}
@media(max-width:520px){.dg-grid{grid-template-columns:1fr}}`;
    document.head.appendChild(el);
  }

  /* ── Data ───────────────────────────────────────────────────── */
  async function refresh() {
    const [c, m] = await Promise.all([
      api('/api/dungeon/catalogue'), api('/api/students/me/dungeon'),
    ]);
    cat = c.data; me = m.data;
    return !!(cat && me);
  }

  const item = id => (cat.items || []).find(i => i.id === id) || { id, name: id };

  /* ── Shop ───────────────────────────────────────────────────── */
  function shopView() {
    const tiers = [['all', 'Everything'], ['beginner', 'Beginner'],
                   ['intermediate', 'Intermediate'], ['relic', 'Relics'],
                   ['utility', 'Utility'], ['reward', 'Real rewards']];
    const slots = [['all', 'Any slot']].concat(
      Object.entries(SLOT_LABEL), [['none', 'Consumables']]);
    // Quest items ride along in the catalogue so the inventory can name and
    // describe them, but they have no price and no shelf — skip them here.
    const list = (cat.items || []).filter(i => !i.quest &&
      (shopTier === 'all' || i.tier === shopTier) &&
      (shopSlot === 'all' || (shopSlot === 'none' ? !i.slot : i.slot === shopSlot)));

    return `
      <div class="dg-filters">
        ${tiers.map(([v, l]) => `<button class="dg-chip ${shopTier === v ? 'on' : ''}"
            data-tier="${v}">${l}</button>`).join('')}
      </div>
      <div class="dg-filters">
        ${slots.map(([v, l]) => `<button class="dg-chip ${shopSlot === v ? 'on' : ''}"
            data-slot="${v}">${l}</button>`).join('')}
      </div>
      <div class="dg-grid">
        ${list.length ? list.map(shopCard).join('')
                      : '<div class="dg-empty">Nothing matches those filters.</div>'}
      </div>`;
  }

  function shopCard(i) {
    const stats = [];
    if (i.shardBonus) stats.push(`+${Math.round(i.shardBonus * 100)}% shards`);
    if (i.maxHp) stats.push(`+${i.maxHp} HP`);
    if (i.defense) stats.push(`+${i.defense} def`);
    if (i.window) stats.push(`${i.window}s window`);
    if (i.evadeChance) stats.push(`${Math.round(i.evadeChance * 100)}% evade`);
    if (i.negateChance) stats.push(`${Math.round(i.negateChance * 100)}% negate`);
    if (i.flatNegate) stats.push(`−${Math.round(i.flatNegate * 100)}% damage`);
    if (i.capacity) stats.push(`${i.capacity} arrows`);
    const withTax = i.price + Math.ceil(i.price * cat.taxRate);
    return `
      <div class="dg-card ${i.locked ? 'locked' : ''}">
        <div class="top">
          ${window.DungeonIcons.item(i.id, 38)}
          <div><div class="nm">${esc(i.name)}</div>
            <div class="dg-tagrow" style="margin-top:4px">
              <span class="dg-tag tier">${i.tier}</span>
              ${i.slot ? `<span class="dg-tag">${SLOT_LABEL[i.slot] || i.slot}</span>` : ''}
              ${i.owned ? `<span class="dg-tag own">owned${i.stackable ? ' ×' + i.owned : ''}</span>` : ''}
            </div>
          </div>
        </div>
        <div class="bl">${esc(i.blurb)}</div>
        ${stats.length ? `<div class="dg-tagrow">${stats.map(s =>
            `<span class="dg-tag">${s}</span>`).join('')}</div>` : ''}
        ${i.locked ? `<div class="dg-note">🔒 ${esc(i.lockReason)}</div>` : ''}
        ${i.saved ? `<div class="dg-save">−20% because you own the
            ${esc(item(i.counterpart).name)}</div>` : ''}
        <div class="dg-price">
          <div>
            ${i.saved || i.luckOff ? `<span class="dg-was">${fmt(i.luckOff ? i.listed : i.full)}</span>` : ''}
            ${gem(i.price)}
            <div class="dg-note" style="font-size:.68rem">
              ${fmt(withTax)} with 13% tax</div>
            ${i.luckOff ? `<div class="dg-save">🍀 ${fmt(i.luckOff)} off for luck</div>` : ''}
          </div>
          <button class="dg-btn" data-buy="${i.id}"
            ${i.locked || (i.owned && !i.stackable) ? 'disabled' : ''}>
            ${i.owned && !i.stackable ? 'Owned' : 'Buy'}</button>
        </div>
      </div>`;
  }

  /* ── Inventory ──────────────────────────────────────────────── */
  function invView() {
    const g = me.gear || {};
    const eq = me.equipped || {};
    const inv = (me.inventory || []).filter(e => e.qty > 0);
    return `
      <div class="dg-stats">
        <div class="dg-stat"><div class="k">Max health</div><div class="v">${fmt(g.maxHp)}</div></div>
        <div class="dg-stat"><div class="k">Defense</div><div class="v">${fmt(g.defense)}</div></div>
        <div class="dg-stat"><div class="k">Shard bonus</div>
          <div class="v">+${Math.round((g.shardBonus || 0) * 100)}%</div></div>
        <div class="dg-stat"><div class="k">Window</div>
          <div class="v">${(g.window || 2).toFixed(2)}s</div></div>
        <div class="dg-stat"><div class="k">Luck</div>
          <div class="v">${me.luck} / ${me.luckMax}</div></div>
      </div>

      <div class="dg-slots">
        ${Object.keys(SLOT_LABEL).map(slot => {
          const id = eq[slot];
          return `<div class="dg-slot ${id ? 'filled' : ''}">
            ${id ? window.DungeonIcons.item(id, 30) : window.DungeonIcons.slot(slot, 30)}
            <div class="sl">${SLOT_LABEL[slot]}</div>
            <div class="in">${id ? esc(item(id).name) : '—'}</div>
            ${id ? `<button data-unequip="${slot}">Take off</button>` : ''}
          </div>`;
        }).join('')}
      </div>

      <div class="dg-box">
        <h4>🍀 Luck — ${me.luck} / ${me.luckMax}
          <span class="dg-note">(${Math.round(me.luckEffectiveness * 100)}% effective)</span></h4>
        <p class="dg-note">Every level amplifies your other gear by 5%, adds +1 to your base
        stats, and widens the doubling window a hair. At ${me.luckMax} it guarantees double
        shards and shrugs off every hit — but the price climbs 12% a level, so it's a
        camp-long project.</p>
        <div style="display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap">
          <button class="dg-btn" id="dg-luck" ${me.nextLuckCost === null ? 'disabled' : ''}>
            ${me.nextLuckCost === null ? 'Maxed out'
              : `Buy luck ${me.luck + 1} · ${fmt(me.nextLuckCost)} points`}</button>
          <span class="dg-note">You have ${fmt(me.points)} points.</span>
        </div>
        <div class="dg-msg" id="dg-luck-msg"></div>
      </div>

      <h4 style="margin:0 0 8px;font-size:.95rem">Carrying (${inv.length})</h4>
      ${inv.length ? `<div class="dg-grid">${inv.map(e => {
        const i = item(e.id);
        return `<div class="dg-card ${i.quest ? 'quest' : ''}">
          <div class="top">${window.DungeonIcons.item(e.id, 34)}
            <div><div class="nm">${esc(i.name)}${e.qty > 1 ? ` ×${e.qty}` : ''}</div>
              ${i.quest ? '<div class="dg-tagrow" style="margin:4px 0"><span class="dg-tag quest">Quest</span></div>' : ''}
              <div class="bl">${esc(i.blurb || '')}</div></div></div>
          ${i.note ? `<div class="dg-price"><span></span>
            <button class="dg-btn ghost" data-read="${e.id}">Read it →</button>
          </div>` : ''}
          ${i.slot ? `<div class="dg-price"><span></span>
            <button class="dg-btn" data-equip="${e.id}" data-eslot="${i.slot}">Equip</button>
          </div>` : ''}
        </div>`;
      }).join('')}</div>` : '<div class="dg-empty">Nothing here yet — the shop is one tab over.</div>'}`;
  }

  /* Anything with a `note` can be re-read from the bag, as many times as
     the camper wants — the point of the note is that it's a standing
     instruction, not a one-off popup they might have clicked past. */
  function openNote(id) {
    const i = item(id);
    if (!i.note) return;
    const wrap = document.createElement('div');
    wrap.className = 'dg-noteveil';
    // Some notes have something on the reverse, and the front is only worth
    // reading because it tells you to turn it over.
    let back = false;
    function paper() {
      wrap.innerHTML = `
      <div class="dg-notepaper" role="dialog" aria-modal="true" aria-label="${esc(i.name)}">
        <div class="dg-notehead">${esc(i.name)}${back ? ' · back' : ''}</div>
        <p class="dg-notebody">${bold(back ? i.noteBack : i.note)}</p>
        ${i.noteBack ? `<button class="dg-btn" data-flip>${
          back ? 'Turn it back over' : 'Turn it over'}</button>` : ''}
        <button class="dg-btn" data-shut>Fold it back up</button>
      </div>`;
      const f = wrap.querySelector('[data-flip]');
      if (f) f.addEventListener('click', () => { back = !back; paper(); });
      wrap.querySelector('[data-shut]').addEventListener('click', () => wrap.remove());
    }
    paper();
    const shut = () => wrap.remove();
    wrap.addEventListener('click', ev => { if (ev.target === wrap) shut(); });
    document.addEventListener('keydown', function esckey(ev) {
      if (ev.key !== 'Escape') return;
      document.removeEventListener('keydown', esckey);
      shut();
    });
    document.body.appendChild(wrap);
  }

  /* ── Receipts ───────────────────────────────────────────────── */
  let receipts = null;
  async function loadReceipts() {
    const p = new URLSearchParams();
    if (recFilter !== 'all') p.set('kind', recFilter);
    if (recFrom) p.set('from', String(new Date(recFrom + 'T00:00:00').getTime()));
    if (recTo) p.set('to', String(new Date(recTo + 'T23:59:59').getTime()));
    receipts = (await api('/api/students/me/receipts?' + p.toString())).data;
  }

  function recView() {
    if (!receipts) return '<div class="dg-empty">Loading…</div>';
    const t = receipts.totals;
    const kinds = [['all', 'All'], ['earning', 'Earnings'], ['purchase', 'Purchases'],
                   ['conversion', 'Conversions'], ['refund', 'Refunds']];
    return `
      <div class="dg-filters">
        ${kinds.map(([v, l]) => `<button class="dg-chip ${recFilter === v ? 'on' : ''}"
            data-kind="${v}">${l}</button>`).join('')}
        <label class="dg-note">From <input type="date" id="dg-from" value="${recFrom}"
          style="padding:4px 7px;border:1px solid var(--dg-line);border-radius:7px"></label>
        <label class="dg-note">To <input type="date" id="dg-to" value="${recTo}"
          style="padding:4px 7px;border:1px solid var(--dg-line);border-radius:7px"></label>
        <button class="dg-btn ghost" id="dg-csv">Export CSV</button>
      </div>
      <div class="dg-stats">
        <div class="dg-stat"><div class="k">Receipts</div><div class="v">${fmt(t.count)}</div></div>
        <div class="dg-stat"><div class="k">Gross</div><div class="v">${fmt(t.gross)}</div></div>
        <div class="dg-stat"><div class="k">Tax withheld</div>
          <div class="v" style="color:#B45309">${fmt(t.tax)}</div></div>
        <div class="dg-stat"><div class="k">Net</div><div class="v">${fmt(t.net)}</div></div>
      </div>
      <p class="dg-note" style="margin-bottom:10px">Greyed-out rows have already been
      claimed on a past return. The <b>Tax</b> column on everything still black is what
      you add up to file.</p>
      ${receipts.receipts.length ? `
      <div style="overflow-x:auto"><table class="dg-table">
        <thead><tr><th>Date</th><th>Type</th><th>What for</th>
          <th class="num">Gross</th><th class="num">Tax</th><th class="num">Net</th>
          <th>ID</th></tr></thead>
        <tbody>${receipts.receipts.map(r => `
          <tr class="${r.reclaimed ? 'done' : ''}">
            <td>${new Date(r.at).toLocaleString(undefined,
                  { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</td>
            <td>${esc(r.kind)}</td>
            <td>${esc(r.description)}</td>
            <td class="num">${fmt(r.gross)}</td>
            <td class="num" style="font-weight:800;color:${r.reclaimed ? 'inherit' : '#B45309'}">
              ${fmt(r.tax)}</td>
            <td class="num">${fmt(r.net)}</td>
            <td style="font-family:ui-monospace,monospace;font-size:.7rem">${esc(r.id.slice(-8))}</td>
          </tr>`).join('')}</tbody>
      </table></div>` : '<div class="dg-empty">No receipts in that range.</div>'}`;
  }

  /* ── Tax ────────────────────────────────────────────────────── */
  let filings = null;
  async function loadFilings() {
    filings = (await api('/api/students/me/tax/filings')).data || [];
  }

  function taxView() {
    const owed = me.unclaimedTax;
    return `
      <div class="dg-box">
        <h4>File a return</h4>
        <p class="dg-note">You have <b>${me.openReceipts}</b> receipt(s) you haven't
        claimed yet. Open the Receipts tab, add up the <b>Tax</b> column on every row
        that isn't greyed out, and type the total here. Get it exactly right and every
        shard of it comes straight back.</p>
        <p class="dg-note">Wrong answers cost you nothing — you'll just be told whether
        you're too high or too low. There's no deadline and no limit on tries.</p>
        <div style="display:flex;gap:8px;align-items:flex-end;margin-top:12px;flex-wrap:wrap">
          <label style="flex:1;min-width:180px">
            <span class="dg-note">Total tax withheld</span>
            <input class="dg-in" id="dg-claim" type="number" min="0" step="1"
                   placeholder="e.g. 1234" ${me.openReceipts ? '' : 'disabled'} />
          </label>
          <button class="dg-btn" id="dg-file" ${me.openReceipts ? '' : 'disabled'}>
            File return</button>
        </div>
        <div class="dg-msg" id="dg-file-msg">${me.openReceipts ? ''
          : 'Nothing to claim right now — earn or spend some shards first.'}</div>
      </div>

      <div class="dg-box dg-help">
        <details open>
          <summary>❓ What is this? (read me)</summary>
          <h5>Why is money missing?</h5>
          <p>Every time you <b>earn</b> shards or <b>spend</b> them, 13% is held back.
          That's tax. Real jobs work the same way: money gets taken off your paycheque
          before you ever see it.</p>

          <h5>How the 13% is worked out</h5>
          <p>Multiply the amount by 0.13 and round up. If you clear a run worth
          <b>1,000</b> shards:</p>
          <table>
            <tr><td>Gross (what you earned)</td><td><b>1,000</b></td></tr>
            <tr><td>Tax (1,000 × 0.13)</td><td><b>130</b></td></tr>
            <tr><td>Net (what lands in your balance)</td><td><b>870</b></td></tr>
          </table>
          <p>On a purchase it's charged on top instead: a 3,500 helmet costs
          3,500 + 455 = <b>3,955</b>.</p>

          <h5>What a receipt is</h5>
          <p>A record of one transaction — when it happened, what it was for, the gross,
          the tax and the net. You get one every single time. Keep them: they're your
          proof of how much was withheld.</p>

          <h5>Adding them up</h5>
          <p>Go to Receipts. Ignore the greyed-out rows (already claimed). Add up the
          <b>Tax</b> column on the rest. Three receipts with tax of 130, 455 and 39
          come to <b>624</b>.</p>

          <h5>Filing, step by step</h5>
          <ol style="line-height:1.7;padding-left:20px">
            <li>Open the Receipts tab.</li>
            <li>Add up the Tax column on every row that isn't greyed out.</li>
            <li>Come back here and type that number in.</li>
            <li>Press <b>File return</b>.</li>
            <li>Exactly right → all of it is refunded to your balance. Wrong → you're
                told whether you're high or low, and you can try again.</li>
          </ol>

          <h5>A worked example</h5>
          <p>Priya finishes a run worth 2,000 shards and buys a 3,000 dagger.</p>
          <table>
            <tr><td>Run earnings</td><td>gross 2,000</td><td>tax <b>260</b></td><td>net 1,740</td></tr>
            <tr><td>Dagger</td><td>gross 3,000</td><td>tax <b>390</b></td><td>paid 3,390</td></tr>
            <tr><td colspan="2"><b>Total tax to claim</b></td>
                <td colspan="2"><b>260 + 390 = 650</b></td></tr>
          </table>
          <p>She types <b>650</b>, files, and 650 shards land back in her balance.</p>
        </details>
      </div>

      ${(filings && filings.length) ? `
      <div class="dg-box">
        <h4>Past filings</h4>
        <table class="dg-table">
          <thead><tr><th>When</th><th class="num">You said</th>
            <th class="num">Actual</th><th>Result</th></tr></thead>
          <tbody>${filings.map(f => `<tr>
            <td>${new Date(f.at).toLocaleString(undefined,
                 { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</td>
            <td class="num">${fmt(f.claimed)}</td>
            <td class="num">${fmt(f.owed)}</td>
            <td>${f.correct ? `<b style="color:#15803d">refunded ${fmt(f.refunded)}</b>`
                            : '<span style="color:#B91C1C">not quite</span>'}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>` : ''}
      <div class="dg-note">Currently withheld and unclaimed: <b>${gem(owed)}</b></div>`;
  }

  /* ── Convert ────────────────────────────────────────────────── */
  function luckLine() {
    const d = Math.round((cat.luckDiscount || 0) * 100);
    if (!d) return '';
    return `<div class="dg-box" style="border-color:rgba(163,230,53,.5)">
      <h4>🍀 Luck ${fmt(cat.luck)}</h4>
      <p class="dg-note"><strong>${d}% off</strong> everything on this page —
      the shelf prices below already have it taken off, and the exchange
      counter's fee does too.</p></div>`;
  }

  function convertBox() {
    const fee = me.conversionFee || 0;
    const done = !!me.cashedOut;
    return `
      <div class="dg-box">
        <h4>Exchange</h4>
        <p class="dg-note">100 shards buy 1 point; 1 point buys 80 shards, and 13%
        tax is withheld whichever way you go. The counter charges a flat
        <strong>${fmt(fee)} points</strong> per trade on top — so trading back and
        forth costs you every time, and there's nothing to farm in the loop.</p>
        <p class="dg-note"><strong>Cashing shards in happens once.</strong>
        ${done
          ? 'You\'ve already done it — your shards buy gear from here on.'
          : 'When you take it, you take <em>all</em> of them at once, and the counter never buys shards from you again. Spend what you want on gear first.'}</p>
        <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px">
          <label style="flex:1;min-width:150px"><span class="dg-note">Points to spend</span>
            <input class="dg-in" id="dg-conv" type="number" min="1" step="1" placeholder="0" /></label>
          <button class="dg-btn ghost" id="dg-p2s">Points → shards</button>
          <button class="dg-btn ghost" id="dg-s2p" ${done ? 'disabled' : ''}>
            ${done ? 'Already cashed out' : `Cash out all ${fmt(me.shards)} shards`}</button>
        </div>
        <div class="dg-msg" id="dg-conv-msg"></div>
      </div>`;
  }

  /* ── Render ─────────────────────────────────────────────────── */
  function render() {
    const root = $('#dungeon-panel');
    if (!root) return;
    root.className = 'dg';
    const pip = me.openReceipts ? `<span class="pip">${me.openReceipts}</span>` : '';
    // `barred` is the server's sentence on why this camper can't go in — the
    // bag, the receipts and the tax return stay open either way, because
    // losing access shouldn't lose you what you already earned or owe.
    const shut = !!me.barred;
    if (shut && (tab === 'shop')) tab = 'inv';
    const tabs = shut ? [['inv', 'Inventory'], ['rec', 'Receipts'], ['tax', 'Tax' + pip]]
                      : [['shop', 'Shop'], ['inv', 'Inventory'], ['rec', 'Receipts'],
                         ['tax', 'Tax' + pip]];
    root.innerHTML = `
      <div class="dg-bal">
        <div><div class="dg-note">Shards</div>
          <div class="dg-gem big">${window.DungeonIcons.shard(20)}${fmt(me.shards)}</div></div>
        <div><div class="dg-note">Points</div>
          <div class="big" style="font-weight:900">${fmt(me.points)}</div></div>
        <div><div class="dg-note">Deepest floor</div>
          <div class="big" style="font-weight:900">${fmt(me.deepest)}</div></div>
        ${shut ? '' : `<a class="dg-btn" href="/infinity/infinity.html" style="text-decoration:none">
          ${me.activeRun ? 'Back into the dungeon →' : 'Enter the dungeon →'}</a>`}
      </div>
      ${shut ? `<div class="dg-box" style="border-color:rgba(185,28,28,.4)">
        <h4>🚧 The dungeon is closed</h4>
        <p class="dg-note">${esc(me.barred)} Your bag, your receipts and your tax
        return are all still here.</p></div>` : ''}
      <div class="dg-tabs">
        ${tabs.map(([k, l]) =>
          `<button class="dg-tab ${tab === k ? 'on' : ''}" data-tab="${k}">${l}</button>`).join('')}
      </div>
      ${tab === 'shop' ? luckLine() + convertBox() + shopView()
        : tab === 'inv' ? invView()
        : tab === 'rec' ? recView()
        : taxView()}`;
    wire(root);
  }

  function wire(root) {
    root.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', async () => {
      tab = b.dataset.tab;
      if (tab === 'rec') await loadReceipts();
      if (tab === 'tax') await loadFilings();
      render();
    }));
    root.querySelectorAll('[data-tier]').forEach(b => b.addEventListener('click', () => {
      shopTier = b.dataset.tier; render();
    }));
    root.querySelectorAll('[data-slot]').forEach(b => b.addEventListener('click', () => {
      shopSlot = b.dataset.slot; render();
    }));
    root.querySelectorAll('[data-kind]').forEach(b => b.addEventListener('click', async () => {
      recFilter = b.dataset.kind; await loadReceipts(); render();
    }));
    const from = root.querySelector('#dg-from'), to = root.querySelector('#dg-to');
    if (from) from.addEventListener('change', async () => {
      recFrom = from.value; await loadReceipts(); render();
    });
    if (to) to.addEventListener('change', async () => {
      recTo = to.value; await loadReceipts(); render();
    });

    const csv = root.querySelector('#dg-csv');
    if (csv) csv.addEventListener('click', () => {
      const rows = [['id', 'date', 'type', 'description', 'gross', 'tax', 'net', 'claimed']]
        .concat((receipts.receipts || []).map(r => [
          r.id, new Date(r.at).toISOString(), r.kind, r.description,
          r.gross, r.tax, r.net, r.reclaimed ? 'yes' : 'no']));
      const body = rows.map(r => r.map(v =>
        `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([body], { type: 'text/csv' }));
      a.download = 'receipts.csv';
      a.click();
      URL.revokeObjectURL(a.href);
    });

    root.querySelectorAll('[data-buy]').forEach(b => b.addEventListener('click', async () => {
      if (busy) return;
      const it = item(b.dataset.buy);
      let qty = 1;
      if (it.stackable) {
        const raw = prompt(`How many ${it.name}?`, '10');
        if (raw === null) return;
        qty = Math.max(1, parseInt(raw, 10) || 1);
      }
      const shelf = it.price * qty;
      const tax = Math.ceil(shelf * cat.taxRate);
      if (!confirm(`${it.name}${qty > 1 ? ' ×' + qty : ''}\n\n` +
                   `Price:  ${fmt(shelf)} shards\n` +
                   `Tax (13%): ${fmt(tax)} shards\n` +
                   `────────────────\n` +
                   `You pay:  ${fmt(shelf + tax)} shards\n\n` +
                   `Balance after: ${fmt(me.shards - shelf - tax)}`)) return;
      busy = true;
      const r = await api('/api/students/me/dungeon/buy', { itemId: it.id, qty });
      busy = false;
      if (!r.ok) return alert(r.error);
      await refresh(); render();
    }));

    root.querySelectorAll('[data-equip]').forEach(b => b.addEventListener('click', async () => {
      const r = await api('/api/students/me/dungeon/equip',
                          { slot: b.dataset.eslot, itemId: b.dataset.equip });
      if (!r.ok) return alert(r.error);
      await refresh(); render();
    }));
    root.querySelectorAll('[data-read]').forEach(b => b.addEventListener('click', () => {
      openNote(b.dataset.read);
    }));
    root.querySelectorAll('[data-unequip]').forEach(b => b.addEventListener('click', async () => {
      const r = await api('/api/students/me/dungeon/equip',
                          { slot: b.dataset.unequip, itemId: null });
      if (!r.ok) return alert(r.error);
      await refresh(); render();
    }));

    const luck = root.querySelector('#dg-luck');
    if (luck) luck.addEventListener('click', async () => {
      const msg = root.querySelector('#dg-luck-msg');
      const r = await api('/api/students/me/dungeon/luck', {});
      if (!r.ok) { msg.className = 'dg-msg err'; msg.textContent = r.error; return; }
      await refresh(); render();
      const m2 = root.querySelector('#dg-luck-msg');
      if (m2) { m2.className = 'dg-msg ok'; m2.textContent = `🍀 Luck is now ${r.data.luck}.`; }
    });

    const conv = root.querySelector('#dg-conv');
    const doConv = async direction => {
      const msg = root.querySelector('#dg-conv-msg');
      const fee = me.conversionFee || 0;
      // Cashing out isn't an amount — it's all of them, once. Only the
      // points→shards direction reads the box.
      const cashOut = direction === 'shards-to-points';
      const amount = cashOut ? me.shards : parseInt(conv.value, 10);
      if (!cashOut && (!amount || amount < 1)) {
        msg.className = 'dg-msg err'; msg.textContent = 'Enter an amount first.'; return;
      }
      const preview = cashOut
        ? `All ${fmt(me.shards)} of your shards → about ${fmt(Math.floor(me.shards / 100))} points before tax.\n\nThis is the only time you can do it.`
        : `${fmt(amount)} points → ${fmt(amount * 80)} shards before tax`;
      if (!confirm(`${preview}\n\n13% is withheld on the way through, and the counter ` +
                   `charges ${fmt(fee)} points to trade. Go ahead?`)) return;
      const r = await api('/api/students/me/dungeon/convert', { direction, amount });
      if (!r.ok) { msg.className = 'dg-msg err'; msg.textContent = r.error; return; }
      await refresh(); render();
      const m2 = root.querySelector('#dg-conv-msg');
      if (m2) {
        m2.className = 'dg-msg ok';
        m2.textContent = `Done — gross ${fmt(r.data.gross)}, tax ${fmt(r.data.tax)}, ` +
                         `counter fee ${fmt(r.data.fee || 0)} pts, ` +
                         `you received ${fmt(r.data.net)}.`;
      }
    };
    const s2p = root.querySelector('#dg-s2p'), p2s = root.querySelector('#dg-p2s');
    if (s2p) s2p.addEventListener('click', () => doConv('shards-to-points'));
    if (p2s) p2s.addEventListener('click', () => doConv('points-to-shards'));

    const file = root.querySelector('#dg-file');
    if (file) file.addEventListener('click', async () => {
      const msg = root.querySelector('#dg-file-msg');
      const raw = root.querySelector('#dg-claim').value;
      if (raw === '') { msg.className = 'dg-msg err'; msg.textContent = 'Enter your total.'; return; }
      const r = await api('/api/students/me/tax/file', { claimed: parseInt(raw, 10) });
      if (!r.ok) { msg.className = 'dg-msg err'; msg.textContent = r.error; return; }
      if (!r.data.correct) {
        msg.className = 'dg-msg err';
        msg.innerHTML = `${esc(r.data.hint)}<br>${esc(r.data.message)}`;
        await loadFilings();
        return;
      }
      await refresh(); await loadFilings(); render();
      const m2 = root.querySelector('#dg-file-msg');
      if (m2) {
        m2.className = 'dg-msg ok';
        m2.innerHTML = `✅ Spot on — <b>${fmt(r.data.refunded)}</b> shards refunded across
                        ${r.data.receiptCount} receipt(s).`;
      }
    });
  }

  /* ── Boot ───────────────────────────────────────────────────── */
  /* ── The bag, on its own ────────────────────────────────────────
     The inventory is not a dungeon feature. Campers pick things up
     elsewhere — the Lime Sword and its note arrive from the Support page
     — and a bag you can only open by finishing a maze is a bag you don't
     have. This renders the equipment slots, the carried items and the
     readable notes into #inventory-panel, with no tabs, no shop, no
     exchange and no way in or out of the dungeon attached. */
  function bagView() {
    const eq = me.equipped || {};
    const inv = (me.inventory || []).filter(e => e.qty > 0);
    return `
      <div class="dg-slots">
        ${Object.keys(SLOT_LABEL).map(slot => {
          const id = eq[slot];
          return `<div class="dg-slot ${id ? 'filled' : ''}">
            ${id ? window.DungeonIcons.item(id, 30) : window.DungeonIcons.slot(slot, 30)}
            <div class="sl">${SLOT_LABEL[slot]}</div>
            <div class="in">${id ? esc(item(id).name) : '—'}</div>
            ${id ? `<button data-unequip="${slot}">Take off</button>` : ''}
          </div>`;
        }).join('')}
      </div>
      <h4 style="margin:0 0 8px;font-size:.95rem">Carrying (${inv.length})</h4>
      ${inv.length ? `<div class="dg-grid">${inv.map(e => {
        const i = item(e.id);
        return `<div class="dg-card ${i.quest ? 'quest' : ''}">
          <div class="top">${window.DungeonIcons.item(e.id, 34)}
            <div><div class="nm">${esc(i.name)}${e.qty > 1 ? ` ×${e.qty}` : ''}</div>
              ${i.quest ? '<div class="dg-tagrow" style="margin:4px 0"><span class="dg-tag quest">Quest</span></div>' : ''}
              <div class="bl">${esc(i.blurb || '')}</div></div></div>
          ${i.note ? `<div class="dg-price"><span></span>
            <button class="dg-btn ghost" data-read="${e.id}">Read it →</button>
          </div>` : ''}
          ${i.slot ? `<div class="dg-price"><span></span>
            <button class="dg-btn" data-equip="${e.id}" data-eslot="${i.slot}">Equip</button>
          </div>` : ''}
        </div>`;
      }).join('')}</div>`
        : '<div class="dg-empty">Nothing here yet. Things you earn around camp end up in here.</div>'}`;
  }

  function renderBag() {
    const root = $('#inventory-panel');
    if (!root || !cat || !me) return;
    root.className = 'dg';
    root.innerHTML = bagView();
    // Equip / unequip / read all work from here; refresh both panels after,
    // since the dungeon panel shows the same slots.
    root.querySelectorAll('[data-read]').forEach(b => b.addEventListener('click', () => {
      openNote(b.dataset.read);
    }));
    root.querySelectorAll('[data-equip]').forEach(b => b.addEventListener('click', async () => {
      const r = await api('/api/students/me/dungeon/equip',
                          { slot: b.dataset.eslot, itemId: b.dataset.equip });
      if (!r.ok) return alert(r.error);
      await refresh(); renderBag(); if ($('#dungeon-panel')) render();
    }));
    root.querySelectorAll('[data-unequip]').forEach(b => b.addEventListener('click', async () => {
      const r = await api('/api/students/me/dungeon/equip',
                          { slot: b.dataset.unequip, itemId: null });
      if (!r.ok) return alert(r.error);
      await refresh(); renderBag(); if ($('#dungeon-panel')) render();
    }));
  }

  window.DungeonShop = {
    /** opts.tab opens straight onto a tab — 'shop' | 'inv' | 'rec' | 'tax'.
        opts.bagOnly renders just the bag, into #inventory-panel. */
    async mount(opts) {
      styles();
      if (opts && opts.tab) tab = opts.tab;
      if (!(await refresh())) return false;
      if (opts && opts.bagOnly) renderBag(); else render();
      return true;
    },
    /** Bag panel only — every camper gets this, dungeon or no dungeon. */
    async mountBag() {
      styles();
      if (!(await refresh())) return false;
      renderBag();
      return true;
    },
    refresh: async () => {
      if (!(await refresh())) return;
      if ($('#inventory-panel')) renderBag();
      if ($('#dungeon-panel')) render();
    },
    /** Scroll to the bag — the dashboard's Inventory shortcut. */
    show(which) {
      const bag = $('#inventory-panel');
      if (bag && bag.innerHTML) {
        bag.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      tab = which || 'inv';
      if (cat && me) render();
      const root = $('#dungeon-panel');
      if (root) root.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
  };
})();
