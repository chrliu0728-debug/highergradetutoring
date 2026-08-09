# Kan Detailings

Marketing site + booking engine + customer/staff messaging, with a Discord bridge
so the crew can run the whole thing from a Discord server.

Black and white only. All body copy is placeholder text about space, and every
block is labelled with a `data-tag` so you can find and replace it later.

## Run it

```bash
npm install
cp .env.example .env      # then edit STAFF_PASSWORD, and the Discord ids if you want the bot
npm start                 # http://localhost:3000
```

`npm run dev` restarts on file changes. Data lands in `data/kan.db` (SQLite, created
on first run, gitignored).

## Pages

| URL | What it is |
| --- | --- |
| `/index.html` | Home |
| `/about.html` | About Us |
| `/support.html` | Support Us |
| `/contact.html` | Contact Us (writes to the `contact_messages` table) |
| `/booking.html` | **Bookings** — the main event |
| `/portal.html` | Customer's booking thread (reference + access code) |
| `/staff.html` | Staff dashboard — all bookings, confirm a window, reply in-thread |

## Editing the copy

Every editable block of text carries a tag:

```html
<h1 data-tag="home.hero.title">We detail cars like they are going to orbit.</h1>
```

Open any page with **`?tags=1`** — e.g. <http://localhost:3000/index.html?tags=1> — and
every tag is drawn on top of the block it labels, so you can see exactly which one to
edit. Tags are namespaced `page.section.slot`.

Copy that is *not* in the HTML:

- Nav labels and footer links — `public/js/site.js` (top of the file).
- Service packages, prices and add-ons — `src/services.js`.
- The vehicle catalog — `src/vehicles.js`.
- Opening hours, window times, capacity — `src/scheduling.js`.

## The booking flow

Five steps; each one stays locked until the one above it is complete.

**1 · Find your exact car.** Four facet columns — **Year**, **Brand** (parent company,
e.g. *Volkswagen Group*), **Make** (the badge, e.g. *Audi*), **Model name** (*A4*). They
are multi-select and they filter each other, with live counts. The customer must tick at
least one box in **all four** columns before the match list opens, and must end on a
single model-year before step 2 unlocks. If the filters collapse to exactly one car it is
selected automatically.

**2 · Service profile.** One of four packages plus any add-ons. Package prices scale with
the size tier of the car chosen in step 1; add-ons are flat.

**3 · Address.** Street, city, region, postal code, plus optional access notes.

**4 · Four ranked windows.** Three-hour windows at 8am / 11am / 2pm / 5pm, Monday to
Saturday. The customer picks **exactly four different** windows; tap order sets the
ranking. Only windows that start **at least 36 hours** from now and **within 16 days** are
shown. Windows already at capacity are greyed out.

**5 · Contact details**, then submit.

All four scheduling rules are re-checked server-side in `src/scheduling.js`
(`validateSlotSelection`), so a hand-written POST cannot skip them.

On success the customer gets a **reference** (`KAN-XXXXXX`) and an **access code**. Those
two together open their thread at `/portal.html`.

### Changing the rules

Everything is at the top of `src/scheduling.js`:

```js
export const SHOP_TIMEZONE = 'America/Vancouver';  // or set SHOP_TIMEZONE in .env
export const SLOT_HOURS      = 3;
export const REQUIRED_SLOTS  = 4;
export const MIN_LEAD_HOURS  = 36;
export const MAX_WINDOW_DAYS = 16;
const WINDOW_START_HOURS = [8, 11, 14, 17];
const CLOSED_WEEKDAYS = new Set([0]);   // 0 = Sunday
export const SLOT_CAPACITY = 2;         // cars per window
```

## Messaging

Every booking opens a thread. The customer writes from `/portal.html`; staff write from
`/staff.html` **or** from Discord. Both sides poll every 4 seconds. System lines
(status changes, window confirmations) are posted automatically.

## The Discord bridge

Set up once:

1. <https://discord.com/developers/applications> → **New Application** → **Bot**.
2. Under **Bot → Privileged Gateway Intents**, switch on **Message Content Intent**.
   Without it the bot cannot read staff replies.
3. Copy the token into `DISCORD_TOKEN` in `.env`.
4. **OAuth2 → URL Generator**: scopes `bot`, permissions *View Channels*, *Send Messages*,
   *Create Public Threads*, *Send Messages in Threads*, *Embed Links*, *Add Reactions*.
   Open the generated URL and add the bot to your server.
5. Right-click your channels → **Copy Channel ID** (Developer Mode must be on) and fill in
   `DISCORD_BOOKINGS_CHANNEL_ID` and `DISCORD_CHAT_CHANNEL_ID`. They can be the same id —
   if they are, the chat thread hangs directly off the booking card.
6. Restart. Startup logs `Discord bridge: on`, and the staff dashboard shows
   `discord: linked`.

What it does:

- A new booking posts a card (customer, vehicle, address, quote, all four ranked windows)
  into the bookings channel, optionally pinging `DISCORD_NOTIFY_ROLE_ID`.
- A thread named `KAN-XXXXXX · Customer Name` opens under it.
- **Anything staff type in that thread goes straight to the customer on the website**, and
  gets a 📨 reaction to confirm it was relayed.
- **Anything the customer types on the website appears in the thread.**

Thread commands:

| Command | Effect |
| --- | --- |
| `!confirm 2` | Lock in the customer's 2nd-choice window and tell them |
| `!status confirmed` | `pending` / `confirmed` / `in_progress` / `completed` / `cancelled` |
| `!info` | Reprint the booking card |
| `!help` | List these |

The bot runs inside the web server process — one `npm start` gets both. With no
`DISCORD_TOKEN` set, the site runs normally and the bridge is simply off.

## Layout

```
server.js              Express app + all routes
src/db.js              SQLite schema and queries
src/vehicles.js        Vehicle catalog (brand / make / name / year / size tier)
src/services.js        Packages, add-ons, pricing
src/scheduling.js      Slot generation and the 36h / 16d / 3h / ×4 rules
src/discord.js         Discord bridge
public/                Static site — pages, css/styles.css, js/{site,booking,chat}.js
```

## API

Public: `GET /api/catalog`, `GET /api/services`, `GET /api/slots`, `POST /api/quote`,
`POST /api/bookings`, `POST /api/contact`.

Customer (needs the `x-access-code` header): `GET|POST /api/bookings/:reference[/messages]`.

Staff (needs the `x-staff-key` header): `GET /api/staff/bookings`,
`GET|POST /api/staff/bookings/:id/messages`, `POST /api/staff/bookings/:id/confirm`,
`POST /api/staff/bookings/:id/status`.

## Before going live

- Set a real `STAFF_PASSWORD` and put the site behind HTTPS. The staff password is a
  single shared secret sent as a header — fine for a small crew, not an accounts system.
- Nothing emails yet. Confirmations live in the thread and on Discord; wire up an email
  provider in `POST /api/bookings` if you want them in an inbox too.
- No payments. The quote is a number, not a charge.
