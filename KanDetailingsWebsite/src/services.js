// Service packages and add-ons. Prices are whole dollars, before the size
// multiplier from src/vehicles.js is applied.

export const PACKAGES = [
  {
    id: 'orbit',
    label: 'Orbit Wash',
    tagline: 'Placeholder tagline about a quick pass through low orbit.',
    duration: '≈ 1.5 hrs',
    base: 89,
    includes: [
      'Placeholder line one about the exterior hand wash',
      'Placeholder line two about wheels and arches',
      'Placeholder line three about the spray sealant',
      'Placeholder line four about glass and door jambs',
    ],
  },
  {
    id: 'lunar',
    label: 'Lunar Interior',
    tagline: 'Placeholder tagline about surveying every crater in the cabin.',
    duration: '≈ 3 hrs',
    base: 179,
    includes: [
      'Placeholder line one about the full vacuum',
      'Placeholder line two about steam extraction',
      'Placeholder line three about leather conditioning',
      'Placeholder line four about the interior UV coat',
    ],
  },
  {
    id: 'eclipse',
    label: 'Total Eclipse',
    tagline: 'Placeholder tagline about the full inside-and-out treatment.',
    duration: '≈ 5 hrs',
    base: 289,
    popular: true,
    includes: [
      'Placeholder line one — everything in Orbit Wash',
      'Placeholder line two — everything in Lunar Interior',
      'Placeholder line three about the one-step machine polish',
      'Placeholder line four about the six-month sealant',
    ],
  },
  {
    id: 'supernova',
    label: 'Supernova Correction',
    tagline: 'Placeholder tagline about resurfacing paint down to the atom.',
    duration: '≈ 9 hrs',
    base: 649,
    includes: [
      'Placeholder line one about the multi-stage paint correction',
      'Placeholder line two about the ceramic coating',
      'Placeholder line three about the trim restoration',
      'Placeholder line four about the aftercare kit',
    ],
  },
];

export const ADDONS = [
  { id: 'pet-hair', label: 'Pet Hair Removal', price: 45, note: 'Placeholder note about fur in the upholstery.' },
  { id: 'engine-bay', label: 'Engine Bay Detail', price: 60, note: 'Placeholder note about degreasing the bay.' },
  { id: 'headlights', label: 'Headlight Restoration', price: 70, note: 'Placeholder note about clearing hazy lenses.' },
  { id: 'ozone', label: 'Odour Neutralising', price: 55, note: 'Placeholder note about the ozone treatment.' },
  { id: 'ceramic-glass', label: 'Ceramic Glass Coat', price: 80, note: 'Placeholder note about rain beading.' },
];

const PACKAGE_BY_ID = new Map(PACKAGES.map((p) => [p.id, p]));
const ADDON_BY_ID = new Map(ADDONS.map((a) => [a.id, a]));

export function findPackage(id) {
  return PACKAGE_BY_ID.get(id) || null;
}

export function findAddon(id) {
  return ADDON_BY_ID.get(id) || null;
}

/**
 * Quote a job. The size multiplier applies to the package only — add-ons are
 * flat rate regardless of what the car is.
 */
export function quote({ packageId, addonIds = [], sizeMultiplier = 1 }) {
  const pkg = findPackage(packageId);
  if (!pkg) return null;
  const packagePrice = Math.round(pkg.base * sizeMultiplier);
  const addons = addonIds.map(findAddon).filter(Boolean);
  const addonsPrice = addons.reduce((sum, a) => sum + a.price, 0);
  return {
    packageId: pkg.id,
    packageLabel: pkg.label,
    packagePrice,
    addons: addons.map((a) => ({ id: a.id, label: a.label, price: a.price })),
    addonsPrice,
    total: packagePrice + addonsPrice,
  };
}
