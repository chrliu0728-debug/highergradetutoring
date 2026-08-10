/* ============================================================
   DUNGEON ITEM ICONS — flat programmer art, one silhouette per
   item family, recoloured per tier.
   ----------------------------------------------------------------
   Deliberately plain: every icon is a 64×64 viewBox, drawn from a
   handful of shapes, readable at 28px and distinguishable by
   outline alone. Tier reads as colour — brown leather, grey steel,
   gold for the intermediate tier — so two daggers sitting next to
   each other are obviously the same thing at different quality.

   Not emoji: these sit in equipment slots at a fixed size, and
   emoji render at wildly different sizes across platforms.
   ============================================================ */
(function () {
  'use strict';

  const C = {
    steel:   '#B8C4D0', steelDark: '#7C8B9C',
    iron:    '#9AA7B4', ironDark:  '#5C6874',
    gold:    '#E3B341', goldDark:  '#A8801E',
    leather: '#A9743F', leatherDk: '#6F4A26',
    wood:    '#8A5A2B', woodDark:  '#5E3C1B',
    grip:    '#4A3524',
    gem:     '#7DD3FC', gemDark:   '#2C7BA0',
    lime:    '#A3E635', limeDark:  '#4D7C0F',
    paper:   '#F1E7C8', ink:       '#8A7A4E',
    grey:    '#94A3B8', greyDark:  '#64748B',
    red:     '#EF4444',
  };

  // Each family takes (a, b) = main colour, accent, and returns inner SVG.
  const FAMILY = {
    dagger: (a, b) => `
      <path d="M32 6 L38 26 L32 34 L26 26 Z" fill="${a}"/>
      <path d="M32 6 L32 34 L26 26 Z" fill="${b}" opacity=".55"/>
      <rect x="20" y="34" width="24" height="5" rx="2" fill="${C.grip}"/>
      <rect x="29" y="39" width="6" height="16" rx="2" fill="${C.grip}"/>
      <circle cx="32" cy="57" r="4" fill="${b}"/>`,
    sword: (a, b) => `
      <path d="M32 3 L37 22 L37 40 L27 40 L27 22 Z" fill="${a}"/>
      <path d="M32 3 L32 40 L27 40 L27 22 Z" fill="${b}" opacity=".5"/>
      <rect x="16" y="40" width="32" height="5" rx="2" fill="${C.grip}"/>
      <rect x="29" y="45" width="6" height="13" rx="2" fill="${C.grip}"/>
      <circle cx="32" cy="59" r="3.5" fill="${b}"/>`,
    hammer: (a, b) => `
      <rect x="12" y="10" width="40" height="20" rx="4" fill="${a}"/>
      <rect x="12" y="10" width="40" height="7" rx="3" fill="${b}" opacity=".5"/>
      <rect x="28" y="30" width="8" height="26" rx="3" fill="${C.grip}"/>`,
    bow: (a, b) => `
      <path d="M20 8 C44 20 44 44 20 56" stroke="${a}" stroke-width="6"
            fill="none" stroke-linecap="round"/>
      <line x1="20" y1="8" x2="20" y2="56" stroke="${b}" stroke-width="2.5"/>
      <path d="M20 32 L40 32" stroke="${b}" stroke-width="2.5"/>`,
    shield: (a, b) => `
      <path d="M32 5 L54 13 V32 C54 45 44 55 32 59 C20 55 10 45 10 32 V13 Z" fill="${a}"/>
      <path d="M32 5 L54 13 V32 C54 45 44 55 32 59 Z" fill="${b}" opacity=".45"/>
      <path d="M32 18 L42 23 V33 C42 40 37 45 32 47 C27 45 22 40 22 33 V23 Z"
            fill="none" stroke="${b}" stroke-width="2.5"/>`,
    quiver: (a, b) => `
      <rect x="20" y="18" width="24" height="40" rx="6" fill="${a}"/>
      <rect x="20" y="18" width="24" height="8" rx="4" fill="${b}"/>
      <path d="M27 18 L27 6 M32 18 L32 3 M37 18 L37 8" stroke="${C.wood}" stroke-width="3"/>
      <path d="M27 6 l-3 4 h6 z M32 3 l-3 4 h6 z M37 8 l-3 4 h6 z" fill="${C.grey}"/>
      <rect x="18" y="34" width="28" height="4" rx="2" fill="${b}"/>`,
    helmet: (a, b) => `
      <path d="M12 34 C12 18 22 9 32 9 C42 9 52 18 52 34 V44 H40 V34
               C40 28 37 25 32 25 C27 25 24 28 24 34 V44 H12 Z" fill="${a}"/>
      <path d="M12 44 h12 v10 h-12 z M40 44 h12 v10 h-12 z" fill="${b}"/>`,
    chest: (a, b) => `
      <path d="M18 12 L32 18 L46 12 L54 20 L48 26 V54 H16 V26 L10 20 Z" fill="${a}"/>
      <path d="M32 18 V54 H16 V26 L10 20 L18 12 Z" fill="${b}" opacity=".4"/>
      <rect x="28" y="26" width="8" height="20" rx="3" fill="${b}"/>`,
    leggings: (a, b) => `
      <path d="M16 8 H48 L45 34 L42 58 H33 L32 38 L31 58 H22 L19 34 Z" fill="${a}"/>
      <path d="M32 8 V38 L31 58 H22 L19 34 L16 8 Z" fill="${b}" opacity=".4"/>`,
    boots: (a, b) => `
      <path d="M18 8 H32 V38 C32 44 36 46 44 46 H50 V56 H18 Z" fill="${a}"/>
      <rect x="16" y="50" width="36" height="7" rx="3" fill="${b}"/>`,
    amulet: (a, b) => `
      <path d="M20 10 C24 26 40 26 44 10" stroke="${b}" stroke-width="3" fill="none"/>
      <circle cx="32" cy="38" r="15" fill="${a}"/>
      <circle cx="32" cy="38" r="7" fill="${b}"/>`,
    necklace: (a, b) => `
      <path d="M14 12 C18 34 46 34 50 12" stroke="${b}" stroke-width="3.5" fill="none"/>
      <path d="M32 30 L44 42 L32 58 L20 42 Z" fill="${a}"/>
      <path d="M32 30 L32 58 L20 42 Z" fill="${b}" opacity=".45"/>`,
    ring: (a, b) => `
      <circle cx="32" cy="40" r="16" fill="none" stroke="${a}" stroke-width="7"/>
      <path d="M32 8 L40 20 L32 28 L24 20 Z" fill="${b}"/>`,
    earring: (a, b) => `
      <circle cx="32" cy="20" r="11" fill="none" stroke="${a}" stroke-width="5"/>
      <path d="M32 33 L39 46 L32 58 L25 46 Z" fill="${b}"/>`,
    arrow: (a, b) => `
      <line x1="32" y1="14" x2="32" y2="54" stroke="${C.wood}" stroke-width="4"/>
      <path d="M32 4 L39 18 H25 Z" fill="${a}"/>
      <path d="M32 46 l-7 10 h14 z" fill="${b}"/>`,
    phone: (a, b) => `
      <rect x="18" y="6" width="28" height="52" rx="6" fill="${a}"/>
      <rect x="22" y="13" width="20" height="34" rx="2" fill="${b}"/>
      <circle cx="32" cy="52" r="3" fill="${b}"/>`,
    shard: (a, b) => `
      <path d="M32 4 L52 24 L32 60 L12 24 Z" fill="${a}"/>
      <path d="M32 4 L32 60 L12 24 Z" fill="${b}" opacity=".5"/>`,
    // A folded note, dog-eared corner and three lines of handwriting.
    note: (a, b) => `
      <path d="M14 8 H42 L52 18 V56 H14 Z" fill="${a}"/>
      <path d="M42 8 L52 18 H42 Z" fill="${b}"/>
      <path d="M21 26 H43 M21 34 H43 M21 42 H35" stroke="${b}" stroke-width="3"
            stroke-linecap="round"/>`,
  };

  // itemId -> [family, main, accent]
  const MAP = {
    crappy_dagger:       ['dagger',   C.grey,    C.greyDark],
    better_dagger:       ['dagger',   C.steel,   C.gold],
    cheap_long_sword:    ['sword',    C.grey,    C.greyDark],
    longer_sword:        ['sword',    C.steel,   C.gold],
    crappy_hammer:       ['hammer',   C.greyDark, C.grey],
    hammer:              ['hammer',   C.iron,    C.gold],
    common_bow:          ['bow',      C.wood,    C.woodDark],
    elven_bow:           ['bow',      C.lime,    C.gold],
    common_shield:       ['shield',   C.grey,    C.greyDark],
    well_crafted_shield: ['shield',   C.iron,    C.gold],
    leather_quiver:      ['quiver',   C.leather, C.leatherDk],
    well_crafted_quiver: ['quiver',   C.leatherDk, C.gold],
    leather_helmet:      ['helmet',   C.leather, C.leatherDk],
    leather_chestplate:  ['chest',    C.leather, C.leatherDk],
    leather_leggings:    ['leggings', C.leather, C.leatherDk],
    leather_boots:       ['boots',    C.leather, C.leatherDk],
    iron_helmet:         ['helmet',   C.iron,    C.ironDark],
    iron_chestplate:     ['chest',    C.iron,    C.ironDark],
    iron_leggings:       ['leggings', C.iron,    C.ironDark],
    iron_boots:          ['boots',    C.iron,    C.ironDark],
    basic_amulet:        ['amulet',   C.gold,    C.gem],
    starting_necklace:   ['necklace', C.gem,     C.gold],
    signet_ring:         ['ring',     C.gold,    C.gemDark],
    earring:             ['earring',  C.gold,    C.gem],
    basic_arrow:         ['arrow',    C.grey,    C.greyDark],
    drill_needle_arrow:  ['arrow',    C.gold,    C.red],
    phone_privileges:    ['phone',    C.greyDark, C.gem],
    lime_sword:          ['sword',    C.lime,    C.limeDark],
    spider_hunt_note:    ['note',     C.paper,   C.ink],
  };

  // Empty equipment slots get a faint outline of what belongs there.
  const SLOT_FAMILY = {
    helmet: 'helmet', chestplate: 'chest', leggings: 'leggings', boots: 'boots',
    weapon: 'sword', back: 'shield', necklace: 'necklace', amulet: 'amulet',
    ring: 'ring', earring: 'earring',
  };

  function svg(inner, size) {
    return `<svg viewBox="0 0 64 64" width="${size}" height="${size}"
                 aria-hidden="true" focusable="false">${inner}</svg>`;
  }

  window.DungeonIcons = {
    /** Icon for an item id. Unknown ids fall back to a shard. */
    item(id, size) {
      const m = MAP[id];
      const [fam, a, b] = m || ['shard', C.gem, C.gemDark];
      return svg(FAMILY[fam](a, b), size || 34);
    },
    /** Greyed-out ghost of whatever belongs in an empty slot. */
    slot(slot, size) {
      const fam = SLOT_FAMILY[slot] || 'shard';
      return `<span style="opacity:.18">${svg(FAMILY[fam]('#94A3B8', '#64748B'), size || 30)}</span>`;
    },
    /** The currency mark itself. */
    shard(size) { return svg(FAMILY.shard(C.gem, C.gemDark), size || 16); },
    families: Object.keys(FAMILY),
  };
})();
