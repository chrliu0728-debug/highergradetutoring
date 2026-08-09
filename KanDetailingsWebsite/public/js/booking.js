// The booking flow.
//
// Step 1 is the important one: four facet columns (year / brand / make / model
// name) that filter each other. The customer has to tick something in every
// column before the match list opens, and has to end up on a single model-year
// before anything else unlocks.

import { api, el, money } from './site.js';
import { mountChat } from './chat.js';

// ---------------------------------------------------------------- state ----

const FACETS = [
  { key: 'year', title: 'Year', search: 'e.g. 2019' },
  { key: 'brand', title: 'Brand (group)', search: 'e.g. Volkswagen Group' },
  { key: 'make', title: 'Make (badge)', search: 'e.g. Audi' },
  { key: 'name', title: 'Model name', search: 'e.g. A4' },
];

const state = {
  vehicles: [],
  sizes: {},
  packages: [],
  addons: [],
  rules: null,
  selected: { year: new Set(), brand: new Set(), make: new Set(), name: new Set() },
  search: { year: '', brand: '', make: '', name: '' },
  vehicle: null,
  packageId: null,
  addonIds: new Set(),
  slots: [], // ISO start strings, ordered by preference
  submitted: null,
};

const $ = (id) => document.getElementById(id);

// ----------------------------------------------------------------- boot ----

boot();

async function boot() {
  try {
    const [catalog, services, slots] = await Promise.all([
      api('/api/catalog'),
      api('/api/services'),
      api('/api/slots'),
    ]);

    state.vehicles = expand(catalog.models);
    state.sizes = catalog.sizes;
    state.packages = services.packages;
    state.addons = services.addons;
    state.rules = slots.rules;
    state.days = slots.days;

    renderFinder();
    renderPackages();
    renderAddons();
    renderPricingCards();
    renderSlots();
    wireForms();
    update();
  } catch (err) {
    document.getElementById('finder').innerHTML =
      `<div class="notice error" style="grid-column:1/-1">Could not load the booking data: ${err.message}</div>`;
  }
}

/** models (one row per model with a year range) -> one row per model-year. */
function expand(models) {
  const out = [];
  for (const m of models) {
    for (let year = m.from; year <= m.to; year++) {
      out.push({
        id: `${m.make}|${m.name}|${year}`.toLowerCase().replace(/\s+/g, '-'),
        brand: m.brand,
        make: m.make,
        name: m.name,
        size: m.size,
        year,
      });
    }
  }
  return out;
}

// ------------------------------------------------------------- facets ------

/** Vehicles passing every facet except `skip`. */
function filtered(skip) {
  return state.vehicles.filter((v) =>
    FACETS.every(({ key }) => {
      if (key === skip) return true;
      const chosen = state.selected[key];
      return chosen.size === 0 || chosen.has(String(v[key]));
    })
  );
}

function matches() {
  return filtered(null);
}

function optionsFor(key) {
  const counts = new Map();
  for (const v of filtered(key)) {
    const value = String(v[key]);
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  // Anything already ticked stays visible even if the other columns rule it out,
  // otherwise the checkbox would vanish out from under the customer's cursor.
  for (const value of state.selected[key]) {
    if (!counts.has(value)) counts.set(value, 0);
  }

  const list = [...counts.entries()].map(([value, count]) => ({ value, count }));
  if (key === 'year') list.sort((a, b) => Number(b.value) - Number(a.value));
  else list.sort((a, b) => a.value.localeCompare(b.value));
  return list;
}

function renderFinder() {
  const host = $('finder');
  host.innerHTML = '';
  for (const facet of FACETS) {
    host.append(renderFacet(facet));
  }
}

function renderFacet(facet) {
  const chosen = state.selected[facet.key];
  const options = optionsFor(facet.key);
  const query = state.search[facet.key].toLowerCase();
  const visible = query ? options.filter((o) => o.value.toLowerCase().includes(query)) : options;

  const list = el('div', { class: 'facet-list' });
  if (!visible.length) {
    list.append(el('p', { class: 'small muted', style: 'padding:.6rem', text: 'Nothing matches that search.' }));
  }
  for (const option of visible) {
    const checked = chosen.has(option.value);
    const box = el('input', { type: 'checkbox', checked });
    box.addEventListener('change', () => {
      if (box.checked) chosen.add(option.value);
      else chosen.delete(option.value);
      // Changing a filter invalidates whatever car was picked.
      state.vehicle = null;
      renderFinder();
      update();
    });
    list.append(
      el('label', { class: `opt${checked ? ' checked' : ''}${option.count === 0 ? ' dimmed' : ''}` }, [
        box,
        el('span', { text: option.value }),
        el('span', { class: 'n', text: String(option.count) }),
      ])
    );
  }

  const searchInput = el('input', {
    type: 'search',
    placeholder: facet.search,
    value: state.search[facet.key],
    'aria-label': `Search ${facet.title}`,
  });
  searchInput.addEventListener('input', () => {
    state.search[facet.key] = searchInput.value;
    renderFinder();
    // Re-focus the box we were typing in, since renderFinder rebuilt the column.
    const again = document.querySelector(`[data-facet="${facet.key}"] input[type="search"]`);
    if (again) {
      again.focus();
      again.setSelectionRange(again.value.length, again.value.length);
    }
  });

  const clear = el('button', {
    class: 'linkish',
    type: 'button',
    text: 'Clear',
    disabled: chosen.size === 0,
  });
  clear.addEventListener('click', () => {
    chosen.clear();
    state.vehicle = null;
    renderFinder();
    update();
  });

  return el('div', { class: 'facet', 'data-facet': facet.key }, [
    el('div', { class: 'facet-head' }, [
      el('div', { class: 'label' }, [
        el('span', { text: facet.title }),
        el('span', { class: 'count', text: chosen.size ? `${chosen.size} picked` : 'any' }),
      ]),
      searchInput,
    ]),
    list,
    el('div', { class: 'facet-foot' }, [
      el('span', { class: 'small muted', text: `${visible.length} shown` }),
      clear,
    ]),
  ]);
}

function allFacetsTouched() {
  return FACETS.every(({ key }) => state.selected[key].size > 0);
}

function renderMatches() {
  const bar = $('match-bar');
  const headline = $('match-headline');
  const sub = $('match-sub');
  const list = $('result-list');

  if (!allFacetsTouched()) {
    const missing = FACETS.filter(({ key }) => state.selected[key].size === 0).map((f) => f.title);
    bar.classList.remove('resolved');
    headline.textContent = 'Keep filtering';
    sub.textContent = `Still need at least one tick in: ${missing.join(', ')}.`;
    list.hidden = true;
    list.innerHTML = '';
    return;
  }

  const found = matches();
  list.hidden = false;
  list.innerHTML = '';

  if (found.length === 0) {
    bar.classList.remove('resolved');
    headline.textContent = 'No vehicle matches those filters';
    sub.textContent = 'Loosen one of the four columns — the combination you ticked does not exist.';
    return;
  }

  if (found.length === 1 && !state.vehicle) {
    state.vehicle = found[0];
  }

  if (state.vehicle) {
    bar.classList.add('resolved');
    const v = state.vehicle;
    headline.textContent = `${v.year} ${v.make} ${v.name}`;
    sub.textContent = `${v.brand} · ${state.sizes[v.size]?.label || v.size}`;
  } else {
    bar.classList.remove('resolved');
    headline.textContent = `${found.length} vehicles match`;
    sub.textContent = 'Narrow the columns further, or pick yours from the list.';
  }

  const CAP = 80;
  for (const v of found.slice(0, CAP)) {
    const button = el('button', {
      type: 'button',
      class: `result${state.vehicle?.id === v.id ? ' selected' : ''}`,
    }, [
      el('span', { text: `${v.year} ${v.make} ${v.name}` }),
      el('span', { class: 'meta', text: `${v.brand} · ${state.sizes[v.size]?.label || v.size}` }),
    ]);
    button.addEventListener('click', () => {
      state.vehicle = state.vehicle?.id === v.id ? null : v;
      update();
    });
    list.append(button);
  }
  if (found.length > CAP) {
    list.append(
      el('p', {
        class: 'small muted',
        style: 'padding:.5rem',
        text: `Showing the first ${CAP} of ${found.length}. Tick more boxes to narrow it down.`,
      })
    );
  }
}

// ----------------------------------------------------------- packages ------

function multiplier() {
  return state.vehicle ? state.sizes[state.vehicle.size]?.multiplier ?? 1 : 1;
}

function packagePrice(pkg) {
  return Math.round(pkg.base * multiplier());
}

function renderPackages() {
  const host = $('packages');
  host.innerHTML = '';
  for (const pkg of state.packages) {
    const selected = state.packageId === pkg.id;
    const card = el('label', { class: `pick${selected ? ' selected' : ''}` }, [
      el('input', { type: 'radio', name: 'package', value: pkg.id, checked: selected }),
      el('div', { class: 'top' }, [
        el('h3', { text: pkg.label }),
        el('span', { class: 'amount', text: money(packagePrice(pkg)) }),
      ]),
      el('p', { class: 'small muted', style: 'margin:.3rem 0 0', text: `${pkg.tagline} · ${pkg.duration}` }),
      el('ul', {}, pkg.includes.map((line) => el('li', { text: line }))),
    ]);
    card.addEventListener('click', () => {
      state.packageId = pkg.id;
      update();
    });
    host.append(card);
  }
}

function renderAddons() {
  const host = $('addons');
  host.innerHTML = '';
  for (const addon of state.addons) {
    const selected = state.addonIds.has(addon.id);
    const box = el('input', { type: 'checkbox', checked: selected });
    const row = el('label', { class: `addon${selected ? ' selected' : ''}` }, [
      box,
      el('span', {}, [
        el('span', { class: 'name', text: addon.label }),
        el('br'),
        el('span', { class: 'note', text: addon.note }),
      ]),
      el('span', { class: 'amount', text: money(addon.price) }),
    ]);
    box.addEventListener('change', () => {
      if (box.checked) state.addonIds.add(addon.id);
      else state.addonIds.delete(addon.id);
      update();
    });
    host.append(row);
  }
}

function renderPricingCards() {
  const host = $('pricing-cards');
  if (!host) return;
  host.innerHTML = '';
  for (const pkg of state.packages) {
    host.append(
      el('article', { class: `card price-card${pkg.popular ? ' featured' : ''}` }, [
        pkg.popular ? el('span', { class: 'flag', text: 'Most booked' }) : null,
        el('h3', { text: pkg.label }),
        el('div', { class: 'price' }, [`$${pkg.base}`, el('small', { text: ' from · compact' })]),
        el('p', { class: 'small muted', text: `${pkg.tagline} · ${pkg.duration}` }),
        el('ul', {}, pkg.includes.map((line) => el('li', { text: line }))),
      ])
    );
  }
}

// -------------------------------------------------------------- slots ------

function renderSlots() {
  const host = $('slot-scroll');
  host.innerHTML = '';

  if (!state.days?.length) {
    host.append(el('p', { class: 'notice', text: 'No windows are open right now. Send us a message instead.' }));
    return;
  }

  for (const day of state.days) {
    const body = el('div', { class: 'slot-day-body' });
    for (const slot of day.slots) {
      const rank = state.slots.indexOf(slot.start);
      const picked = rank !== -1;
      const atLimit = state.slots.length >= (state.rules?.requiredSlots ?? 4);
      const button = el('button', {
        type: 'button',
        class: `slot${picked ? ' picked' : ''}`,
        disabled: slot.full || (atLimit && !picked),
      }, [
        el('span', { class: 'rank', text: picked ? String(rank + 1) : '·' }),
        el('span', { text: slot.label }),
        el('span', { class: 'left', text: slot.full ? 'full' : `${slot.remaining} left` }),
      ]);
      button.addEventListener('click', () => toggleSlot(slot.start));
      body.append(button);
    }
    host.append(
      el('div', { class: 'slot-day' }, [el('div', { class: 'slot-day-head', text: day.label }), body])
    );
  }
}

function toggleSlot(start) {
  const at = state.slots.indexOf(start);
  if (at !== -1) state.slots.splice(at, 1);
  else if (state.slots.length < (state.rules?.requiredSlots ?? 4)) state.slots.push(start);
  update();
}

function slotLabel(start) {
  for (const day of state.days || []) {
    const found = day.slots.find((s) => s.start === start);
    if (found) return `${day.label} · ${found.label}`;
  }
  return start;
}

function renderPicked() {
  const host = $('picked-list');
  const need = state.rules?.requiredSlots ?? 4;
  host.innerHTML = '';
  for (let i = 0; i < need; i++) {
    const start = state.slots[i];
    if (!start) {
      host.append(
        el('div', { class: 'picked-row empty' }, [
          el('span', { class: 'rank', style: 'background:transparent;color:var(--grey-3);border:1px solid var(--line)', text: `#${i + 1}` }),
          el('span', { class: 'small', text: 'Not chosen yet' }),
        ])
      );
      continue;
    }
    const remove = el('button', { class: 'linkish', type: 'button', text: 'Remove', style: 'margin-left:auto' });
    remove.addEventListener('click', () => toggleSlot(start));
    host.append(
      el('div', { class: 'picked-row' }, [
        el('span', { class: 'rank', text: `#${i + 1}` }),
        el('span', { class: 'small', text: slotLabel(start) }),
        remove,
      ])
    );
  }

  const rules = state.rules;
  $('slot-hint').textContent = rules
    ? `${state.slots.length} of ${rules.requiredSlots} chosen · each window is ${rules.slotHours} hours · earliest is ${rules.minLeadHours}h from now · latest is ${rules.maxWindowDays} days out · times shown in ${rules.timezone}.`
    : '';
}

// ------------------------------------------------------------ progress -----

function addressValues() {
  return {
    line1: $('addr-line1').value.trim(),
    line2: $('addr-line2').value.trim(),
    city: $('addr-city').value.trim(),
    region: $('addr-region').value.trim(),
    postalCode: $('addr-postal').value.trim(),
    notes: $('addr-notes').value.trim(),
  };
}

function customerValues() {
  return {
    fullName: $('cust-name').value.trim(),
    email: $('cust-email').value.trim(),
    phone: $('cust-phone').value.trim(),
    vehicleColour: $('veh-colour').value.trim(),
    plate: $('veh-plate').value.trim(),
    notes: $('cust-notes').value.trim(),
  };
}

function progress() {
  const address = addressValues();
  const customer = customerValues();
  return {
    vehicle: !!state.vehicle,
    service: !!state.packageId,
    address: !!(address.line1 && address.city && address.region && address.postalCode),
    slots: state.slots.length === (state.rules?.requiredSlots ?? 4),
    details: !!(
      customer.fullName &&
      /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(customer.email) &&
      customer.phone
    ),
  };
}

const STEP_ORDER = [
  ['vehicle', 'Vehicle', 'step-vehicle'],
  ['service', 'Service', 'step-service'],
  ['address', 'Address', 'step-address'],
  ['slots', 'Windows', 'step-slots'],
  ['details', 'Details', 'step-details'],
];

function renderSteps(done) {
  const host = $('steps');
  host.innerHTML = '';
  let currentFound = false;
  for (const [key, label] of STEP_ORDER) {
    let cls = 'step';
    if (done[key]) cls += ' done';
    else if (!currentFound) {
      cls += ' current';
      currentFound = true;
    }
    host.append(el('span', { class: cls, text: label }));
  }
}

function applyLocks(done) {
  // Each section unlocks only once every earlier one is complete.
  let unlocked = true;
  for (const [key, , id] of STEP_ORDER) {
    const section = $(id);
    section.classList.toggle('locked', !unlocked);
    unlocked = unlocked && done[key];
  }
}

function renderSummary(done) {
  const host = $('summary-body');
  host.innerHTML = '';

  if (!state.vehicle) {
    host.append(el('p', { class: 'muted small', text: 'Your booking will build up here as you go.' }));
  } else {
    const v = state.vehicle;
    host.append(
      el('div', { class: 'stack' }, [
        el('div', {}, [
          el('div', { class: 'label', style: 'margin-bottom:.15rem', text: 'Vehicle' }),
          el('div', { text: `${v.year} ${v.make} ${v.name}` }),
          el('div', { class: 'small muted', text: `${v.brand} · ${state.sizes[v.size]?.label || v.size}` }),
        ]),
      ])
    );
  }

  const pkg = state.packages.find((p) => p.id === state.packageId);
  if (pkg) {
    host.append(el('div', { class: 'summary-line' }, [
      el('span', { text: pkg.label }),
      el('span', { text: money(packagePrice(pkg)) }),
    ]));
    for (const id of state.addonIds) {
      const addon = state.addons.find((a) => a.id === id);
      if (!addon) continue;
      host.append(el('div', { class: 'summary-line' }, [
        el('span', { class: 'muted', text: addon.label }),
        el('span', { class: 'muted', text: money(addon.price) }),
      ]));
    }
    host.append(el('div', { class: 'summary-line total' }, [
      el('span', { text: 'Quote' }),
      el('span', { text: money(total()) }),
    ]));
  }

  if (state.slots.length) {
    host.append(el('div', { class: 'label', style: 'margin:1rem 0 .3rem', text: 'Ranked windows' }));
    state.slots.forEach((start, index) => {
      host.append(el('div', { class: 'small muted', text: `${index + 1}. ${slotLabel(start)}` }));
    });
  }

  const outstanding = STEP_ORDER.filter(([key]) => !done[key]);
  const button = $('submit-booking');
  button.disabled = outstanding.length > 0;
  $('submit-hint').textContent = outstanding.length
    ? `Still to do: ${outstanding.map(([, label]) => label).join(', ')}.`
    : 'Everything checks out — send it.';
}

function total() {
  const pkg = state.packages.find((p) => p.id === state.packageId);
  if (!pkg) return 0;
  let sum = packagePrice(pkg);
  for (const id of state.addonIds) {
    sum += state.addons.find((a) => a.id === id)?.price || 0;
  }
  return sum;
}

function update() {
  // renderMatches() first: when the filters collapse to a single car it selects
  // it, and progress() has to see that before the locks are recalculated.
  renderMatches();
  const done = progress();
  renderPackages();
  renderAddons();
  renderSlots();
  renderPicked();
  renderSteps(done);
  applyLocks(done);
  renderSummary(done);
}

// -------------------------------------------------------------- submit -----

function wireForms() {
  for (const id of [
    'addr-line1', 'addr-line2', 'addr-city', 'addr-region', 'addr-postal', 'addr-notes',
    'cust-name', 'cust-email', 'cust-phone', 'veh-colour', 'veh-plate', 'cust-notes',
  ]) {
    $(id).addEventListener('input', () => update());
  }

  $('reset-finder').addEventListener('click', () => {
    for (const facet of FACETS) {
      state.selected[facet.key].clear();
      state.search[facet.key] = '';
    }
    state.vehicle = null;
    renderFinder();
    update();
  });

  $('submit-booking').addEventListener('click', submit);
}

function showError(message) {
  const box = $('submit-error');
  box.hidden = false;
  box.className = 'notice error';
  box.textContent = message;
  box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function submit() {
  const button = $('submit-booking');
  const customer = customerValues();
  button.disabled = true;
  button.textContent = 'Sending…';
  $('submit-error').hidden = true;

  try {
    const result = await api('/api/bookings', {
      method: 'POST',
      body: {
        fullName: customer.fullName,
        email: customer.email,
        phone: customer.phone,
        address: addressValues(),
        vehicleId: state.vehicle.id,
        vehicleColour: customer.vehicleColour,
        plate: customer.plate,
        packageId: state.packageId,
        addons: [...state.addonIds],
        slots: state.slots,
        notes: customer.notes,
      },
    });
    state.submitted = result;
    try {
      localStorage.setItem(
        'kan.lastBooking',
        JSON.stringify({ reference: result.reference, accessCode: result.accessCode })
      );
    } catch {
      /* private browsing — not important */
    }
    showConfirmation(result);
  } catch (err) {
    showError(err.message);
    button.disabled = false;
    button.textContent = 'Confirm booking';
    // A slot may have filled while they were typing — refresh the grid.
    api('/api/slots')
      .then((data) => {
        state.days = data.days;
        state.slots = state.slots.filter((start) =>
          data.days.some((d) => d.slots.some((s) => s.start === start && !s.full))
        );
        update();
      })
      .catch(() => {});
  }
}

function showConfirmation(result) {
  for (const [, , id] of STEP_ORDER) $(id).hidden = true;
  document.querySelector('.booking-grid').hidden = true;
  $('steps').hidden = true;

  const host = $('confirmation');
  host.hidden = false;

  const b = result.booking;
  const chatBox = el('div', { id: 'chat' });

  host.append(
    el('div', { class: 'panel' }, [
      el('span', { class: 'eyebrow', text: 'Booking received' }),
      el('h2', { text: `You are on the manifest — ${result.reference}` }),
      el('p', { class: 'lede' }, [
        'Keep this reference and access code — together they open your booking thread at ',
        el('a', { href: '/portal.html', text: 'Track my booking' }),
        '.',
      ]),
      el('div', { class: 'grid two', style: 'margin:1.2rem 0' }, [
        el('div', { class: 'panel' }, [
          el('div', { class: 'label', text: 'Reference' }),
          el('div', { class: 'mono', style: 'font-size:1.4rem;color:#fff', text: result.reference }),
        ]),
        el('div', { class: 'panel' }, [
          el('div', { class: 'label', text: 'Access code' }),
          el('div', { class: 'mono', style: 'font-size:1.4rem;color:#fff', text: result.accessCode }),
        ]),
      ]),
      el('div', { class: 'grid two' }, [
        el('div', {}, [
          el('div', { class: 'label', text: 'Vehicle' }),
          el('p', { text: `${b.vehicle.year} ${b.vehicle.make} ${b.vehicle.name}` }),
          el('div', { class: 'label', text: 'Service' }),
          el('p', { text: `${b.service.packageLabel} · ${money(b.service.quotedTotal)}` }),
        ]),
        el('div', {}, [
          el('div', { class: 'label', text: 'Your ranked windows' }),
          el('ol', { class: 'small', style: 'padding-left:1.1rem;color:var(--grey-2)' },
            b.slots.map((s) => el('li', { text: s.label }))),
        ]),
      ]),
      el('p', { class: 'hint', text: 'A detailer will confirm one of these four windows in the thread below.' }),
    ]),
    el('div', { class: 'panel', style: 'margin-top:1.2rem' }, [
      el('span', { class: 'eyebrow', text: 'Talk to the crew' }),
      chatBox,
    ])
  );

  const headers = { 'x-access-code': result.accessCode };
  mountChat(chatBox, {
    mineIs: 'customer',
    load: (since) => api(`/api/bookings/${result.reference}/messages?since=${since}`, { headers }),
    send: (body) =>
      api(`/api/bookings/${result.reference}/messages`, { method: 'POST', headers, body: { body } }),
  });

  host.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
