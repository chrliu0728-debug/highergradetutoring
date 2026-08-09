// The vehicle catalog that powers the four-facet car finder on the bookings page.
//
// The four facets the customer filters by:
//   brand -> the parent company / group   (e.g. "Volkswagen Group")
//   make  -> the marque on the badge      (e.g. "Audi")
//   name  -> the model name               (e.g. "A4")
//   year  -> a model year the car was sold in
//
// Rows are written compactly as [make, name, firstYear, lastYear, size] and
// expanded into one record per model-year below. `size` drives the price tier.

const CURRENT_YEAR = 2026;

// size -> label shown to the customer + the multiplier applied to package prices.
export const SIZE_TIERS = {
  compact: { label: 'Compact', multiplier: 1.0 },
  sedan: { label: 'Sedan / Coupe', multiplier: 1.1 },
  suv: { label: 'SUV / Crossover', multiplier: 1.25 },
  truck: { label: 'Truck / Van', multiplier: 1.45 },
};

const CATALOG = {
  'Toyota Motor Corporation': {
    Toyota: [
      ['Corolla', 1998, CURRENT_YEAR, 'compact'],
      ['Camry', 1998, CURRENT_YEAR, 'sedan'],
      ['Prius', 2001, CURRENT_YEAR, 'compact'],
      ['RAV4', 1998, CURRENT_YEAR, 'suv'],
      ['Highlander', 2001, CURRENT_YEAR, 'suv'],
      ['4Runner', 1998, CURRENT_YEAR, 'suv'],
      ['Tacoma', 1998, CURRENT_YEAR, 'truck'],
      ['Tundra', 2000, CURRENT_YEAR, 'truck'],
      ['Sienna', 1998, CURRENT_YEAR, 'truck'],
      ['Supra', 2020, CURRENT_YEAR, 'sedan'],
    ],
    Lexus: [
      ['IS 350', 2006, CURRENT_YEAR, 'sedan'],
      ['ES 350', 2007, CURRENT_YEAR, 'sedan'],
      ['RX 350', 2007, CURRENT_YEAR, 'suv'],
      ['NX 300', 2015, CURRENT_YEAR, 'suv'],
      ['GX 460', 2010, CURRENT_YEAR, 'suv'],
      ['LS 500', 2018, CURRENT_YEAR, 'sedan'],
    ],
  },
  'Honda Motor Co.': {
    Honda: [
      ['Civic', 1998, CURRENT_YEAR, 'compact'],
      ['Accord', 1998, CURRENT_YEAR, 'sedan'],
      ['Fit', 2007, 2020, 'compact'],
      ['CR-V', 1998, CURRENT_YEAR, 'suv'],
      ['Pilot', 2003, CURRENT_YEAR, 'suv'],
      ['HR-V', 2016, CURRENT_YEAR, 'suv'],
      ['Odyssey', 1999, CURRENT_YEAR, 'truck'],
      ['Ridgeline', 2006, CURRENT_YEAR, 'truck'],
    ],
    Acura: [
      ['TLX', 2015, CURRENT_YEAR, 'sedan'],
      ['ILX', 2013, 2022, 'sedan'],
      ['MDX', 2001, CURRENT_YEAR, 'suv'],
      ['RDX', 2007, CURRENT_YEAR, 'suv'],
      ['Integra', 1998, 2001, 'compact'],
      ['Integra', 2023, CURRENT_YEAR, 'compact'],
    ],
  },
  'Hyundai Motor Group': {
    Hyundai: [
      ['Elantra', 1998, CURRENT_YEAR, 'compact'],
      ['Sonata', 1998, CURRENT_YEAR, 'sedan'],
      ['Tucson', 2005, CURRENT_YEAR, 'suv'],
      ['Santa Fe', 2001, CURRENT_YEAR, 'suv'],
      ['Palisade', 2020, CURRENT_YEAR, 'suv'],
      ['Ioniq 5', 2022, CURRENT_YEAR, 'suv'],
      ['Kona', 2018, CURRENT_YEAR, 'suv'],
    ],
    Kia: [
      ['Forte', 2010, CURRENT_YEAR, 'compact'],
      ['Optima', 2001, 2020, 'sedan'],
      ['K5', 2021, CURRENT_YEAR, 'sedan'],
      ['Sportage', 1998, CURRENT_YEAR, 'suv'],
      ['Sorento', 2003, CURRENT_YEAR, 'suv'],
      ['Telluride', 2020, CURRENT_YEAR, 'suv'],
      ['Carnival', 2022, CURRENT_YEAR, 'truck'],
      ['EV6', 2022, CURRENT_YEAR, 'suv'],
    ],
    Genesis: [
      ['G70', 2019, CURRENT_YEAR, 'sedan'],
      ['G80', 2017, CURRENT_YEAR, 'sedan'],
      ['GV70', 2022, CURRENT_YEAR, 'suv'],
      ['GV80', 2021, CURRENT_YEAR, 'suv'],
    ],
  },
  'Volkswagen Group': {
    Volkswagen: [
      ['Golf', 1998, CURRENT_YEAR, 'compact'],
      ['Jetta', 1998, CURRENT_YEAR, 'compact'],
      ['Passat', 1998, 2022, 'sedan'],
      ['Tiguan', 2009, CURRENT_YEAR, 'suv'],
      ['Atlas', 2018, CURRENT_YEAR, 'suv'],
      ['ID.4', 2021, CURRENT_YEAR, 'suv'],
    ],
    Audi: [
      ['A3', 2006, CURRENT_YEAR, 'compact'],
      ['A4', 1998, CURRENT_YEAR, 'sedan'],
      ['A6', 1998, CURRENT_YEAR, 'sedan'],
      ['Q3', 2015, CURRENT_YEAR, 'suv'],
      ['Q5', 2009, CURRENT_YEAR, 'suv'],
      ['Q7', 2007, CURRENT_YEAR, 'suv'],
      ['e-tron', 2019, CURRENT_YEAR, 'suv'],
    ],
    Porsche: [
      ['911', 1998, CURRENT_YEAR, 'sedan'],
      ['Cayenne', 2003, CURRENT_YEAR, 'suv'],
      ['Macan', 2015, CURRENT_YEAR, 'suv'],
      ['Panamera', 2010, CURRENT_YEAR, 'sedan'],
      ['Taycan', 2020, CURRENT_YEAR, 'sedan'],
    ],
  },
  'BMW Group': {
    BMW: [
      ['3 Series', 1998, CURRENT_YEAR, 'sedan'],
      ['5 Series', 1998, CURRENT_YEAR, 'sedan'],
      ['7 Series', 1998, CURRENT_YEAR, 'sedan'],
      ['X1', 2013, CURRENT_YEAR, 'suv'],
      ['X3', 2004, CURRENT_YEAR, 'suv'],
      ['X5', 2000, CURRENT_YEAR, 'suv'],
      ['i4', 2022, CURRENT_YEAR, 'sedan'],
    ],
    Mini: [
      ['Cooper', 2002, CURRENT_YEAR, 'compact'],
      ['Countryman', 2011, CURRENT_YEAR, 'suv'],
    ],
  },
  'Mercedes-Benz Group': {
    'Mercedes-Benz': [
      ['A-Class', 2019, 2022, 'compact'],
      ['C-Class', 1998, CURRENT_YEAR, 'sedan'],
      ['E-Class', 1998, CURRENT_YEAR, 'sedan'],
      ['S-Class', 1998, CURRENT_YEAR, 'sedan'],
      ['GLC', 2016, CURRENT_YEAR, 'suv'],
      ['GLE', 2016, CURRENT_YEAR, 'suv'],
      ['Sprinter', 2003, CURRENT_YEAR, 'truck'],
    ],
  },
  Stellantis: {
    Jeep: [
      ['Wrangler', 1998, CURRENT_YEAR, 'suv'],
      ['Grand Cherokee', 1998, CURRENT_YEAR, 'suv'],
      ['Cherokee', 1998, 2023, 'suv'],
      ['Compass', 2007, CURRENT_YEAR, 'suv'],
      ['Gladiator', 2020, CURRENT_YEAR, 'truck'],
    ],
    Dodge: [
      ['Charger', 2006, CURRENT_YEAR, 'sedan'],
      ['Challenger', 2008, 2023, 'sedan'],
      ['Durango', 1998, CURRENT_YEAR, 'suv'],
      ['Grand Caravan', 1998, 2020, 'truck'],
    ],
    Ram: [
      ['1500', 2011, CURRENT_YEAR, 'truck'],
      ['2500', 2011, CURRENT_YEAR, 'truck'],
      ['ProMaster', 2014, CURRENT_YEAR, 'truck'],
    ],
    Chrysler: [
      ['300', 2005, 2023, 'sedan'],
      ['Pacifica', 2017, CURRENT_YEAR, 'truck'],
    ],
  },
  'Ford Motor Company': {
    Ford: [
      ['Focus', 2000, 2018, 'compact'],
      ['Fusion', 2006, 2020, 'sedan'],
      ['Mustang', 1998, CURRENT_YEAR, 'sedan'],
      ['Escape', 2001, CURRENT_YEAR, 'suv'],
      ['Explorer', 1998, CURRENT_YEAR, 'suv'],
      ['Bronco', 2021, CURRENT_YEAR, 'suv'],
      ['F-150', 1998, CURRENT_YEAR, 'truck'],
      ['Transit', 2015, CURRENT_YEAR, 'truck'],
      ['Mustang Mach-E', 2021, CURRENT_YEAR, 'suv'],
    ],
    Lincoln: [
      ['MKZ', 2007, 2020, 'sedan'],
      ['Navigator', 1998, CURRENT_YEAR, 'truck'],
      ['Corsair', 2020, CURRENT_YEAR, 'suv'],
    ],
  },
  'General Motors': {
    Chevrolet: [
      ['Cruze', 2011, 2019, 'compact'],
      ['Malibu', 1998, CURRENT_YEAR, 'sedan'],
      ['Impala', 1998, 2020, 'sedan'],
      ['Equinox', 2005, CURRENT_YEAR, 'suv'],
      ['Traverse', 2009, CURRENT_YEAR, 'suv'],
      ['Tahoe', 1998, CURRENT_YEAR, 'truck'],
      ['Silverado 1500', 1999, CURRENT_YEAR, 'truck'],
      ['Corvette', 1998, CURRENT_YEAR, 'sedan'],
      ['Bolt EV', 2017, 2023, 'compact'],
    ],
    GMC: [
      ['Sierra 1500', 1999, CURRENT_YEAR, 'truck'],
      ['Yukon', 1998, CURRENT_YEAR, 'truck'],
      ['Acadia', 2007, CURRENT_YEAR, 'suv'],
      ['Terrain', 2010, CURRENT_YEAR, 'suv'],
    ],
    Cadillac: [
      ['CTS', 2003, 2019, 'sedan'],
      ['XT5', 2017, CURRENT_YEAR, 'suv'],
      ['Escalade', 1999, CURRENT_YEAR, 'truck'],
      ['Lyriq', 2023, CURRENT_YEAR, 'suv'],
    ],
    Buick: [
      ['Encore', 2013, CURRENT_YEAR, 'suv'],
      ['Enclave', 2008, CURRENT_YEAR, 'suv'],
    ],
  },
  'Nissan Motor Co.': {
    Nissan: [
      ['Sentra', 1998, CURRENT_YEAR, 'compact'],
      ['Altima', 1998, CURRENT_YEAR, 'sedan'],
      ['Maxima', 1998, 2023, 'sedan'],
      ['Rogue', 2008, CURRENT_YEAR, 'suv'],
      ['Pathfinder', 1998, CURRENT_YEAR, 'suv'],
      ['Frontier', 1998, CURRENT_YEAR, 'truck'],
      ['Leaf', 2011, CURRENT_YEAR, 'compact'],
      ['370Z', 2009, 2020, 'sedan'],
    ],
    Infiniti: [
      ['Q50', 2014, CURRENT_YEAR, 'sedan'],
      ['QX60', 2014, CURRENT_YEAR, 'suv'],
      ['QX80', 2014, CURRENT_YEAR, 'truck'],
    ],
  },
  'Tesla, Inc.': {
    Tesla: [
      ['Model S', 2013, CURRENT_YEAR, 'sedan'],
      ['Model 3', 2018, CURRENT_YEAR, 'sedan'],
      ['Model X', 2016, CURRENT_YEAR, 'suv'],
      ['Model Y', 2020, CURRENT_YEAR, 'suv'],
      ['Cybertruck', 2024, CURRENT_YEAR, 'truck'],
    ],
  },
  'Subaru Corporation': {
    Subaru: [
      ['Impreza', 1998, CURRENT_YEAR, 'compact'],
      ['WRX', 2002, CURRENT_YEAR, 'sedan'],
      ['Legacy', 1998, CURRENT_YEAR, 'sedan'],
      ['Outback', 1998, CURRENT_YEAR, 'suv'],
      ['Forester', 1998, CURRENT_YEAR, 'suv'],
      ['Crosstrek', 2013, CURRENT_YEAR, 'suv'],
      ['BRZ', 2013, CURRENT_YEAR, 'sedan'],
    ],
  },
  'Mazda Motor Corporation': {
    Mazda: [
      ['Mazda3', 2004, CURRENT_YEAR, 'compact'],
      ['Mazda6', 2003, 2021, 'sedan'],
      ['MX-5 Miata', 1998, CURRENT_YEAR, 'compact'],
      ['CX-5', 2013, CURRENT_YEAR, 'suv'],
      ['CX-9', 2007, 2023, 'suv'],
      ['CX-50', 2023, CURRENT_YEAR, 'suv'],
    ],
  },
  'Volvo Cars': {
    Volvo: [
      ['S60', 2001, CURRENT_YEAR, 'sedan'],
      ['XC40', 2019, CURRENT_YEAR, 'suv'],
      ['XC60', 2010, CURRENT_YEAR, 'suv'],
      ['XC90', 2003, CURRENT_YEAR, 'suv'],
    ],
  },
  'Tata Motors': {
    'Land Rover': [
      ['Range Rover', 1998, CURRENT_YEAR, 'suv'],
      ['Range Rover Sport', 2006, CURRENT_YEAR, 'suv'],
      ['Discovery', 1998, CURRENT_YEAR, 'suv'],
      ['Defender', 2020, CURRENT_YEAR, 'suv'],
    ],
    Jaguar: [
      ['XE', 2017, 2020, 'sedan'],
      ['XF', 2009, CURRENT_YEAR, 'sedan'],
      ['F-Pace', 2017, CURRENT_YEAR, 'suv'],
    ],
  },
};

export function vehicleId(make, name, year) {
  return `${make}|${name}|${year}`.toLowerCase().replace(/\s+/g, '-');
}

function buildCatalog() {
  const vehicles = [];
  const models = [];
  for (const [brand, makes] of Object.entries(CATALOG)) {
    for (const [make, entries] of Object.entries(makes)) {
      for (const [name, from, to, size] of entries) {
        models.push({ brand, make, name, size, from, to });
        for (let year = from; year <= to; year++) {
          vehicles.push({ id: vehicleId(make, name, year), brand, make, name, year, size });
        }
      }
    }
  }
  return { vehicles, models };
}

const built = buildCatalog();

/** Every sellable model-year, one row each. */
export const VEHICLES = built.vehicles;

/**
 * The same catalog collapsed to one row per model with a year range — this is
 * what gets shipped to the browser, since the expanded list is ~4k rows.
 */
export const MODELS = built.models;

const BY_ID = new Map(VEHICLES.map((v) => [v.id, v]));

export function findVehicle(id) {
  return BY_ID.get(String(id || '').toLowerCase()) || null;
}

/** Distinct values for each facet, sorted the way the UI wants to show them. */
export const FACETS = {
  brand: [...new Set(VEHICLES.map((v) => v.brand))].sort((a, b) => a.localeCompare(b)),
  make: [...new Set(VEHICLES.map((v) => v.make))].sort((a, b) => a.localeCompare(b)),
  name: [...new Set(VEHICLES.map((v) => v.name))].sort((a, b) => a.localeCompare(b)),
  year: [...new Set(VEHICLES.map((v) => v.year))].sort((a, b) => b - a),
};
