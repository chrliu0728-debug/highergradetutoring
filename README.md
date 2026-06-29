# HigherGrade Tutoring

Math camp website — static frontend + Flask/SQLite backend.

In production, Caddy serves the HTML/CSS/JS and reverse-proxies `/api/*` to the Flask app.
Locally, an Express dev server does the same thing on port 3000.

## Prerequisites

- **Node.js 18+** — [nodejs.org](https://nodejs.org)
- **Python 3.9+** — already on macOS; `brew install python` if needed

## Quick start

```bash
# 1. Install Node dependencies (once)
npm install

# 2. Create the Python virtual environment and install Flask (once)
npm run setup

# 3. Start the site
npm run dev
```

Open **http://localhost:3000** in your browser.

`npm run dev` runs two processes together:

| Process | What it does |
|---------|--------------|
| `flask` | Flask API on `http://127.0.0.1:5000` — handles all `/api/*` requests |
| `vite`  | Vite dev server on port 3000 — serves static files and proxies `/api/*` to Flask |

Press `Ctrl-C` to stop both.

## Run a single process

```bash
npm run flask   # Flask API only
npx vite        # Vite dev server only (no API)
```

## Local database

The Flask backend stores data in `math_camp/server/dev.db` (SQLite).
This file is created automatically on first run and is git-ignored.
The production database lives at `/var/lib/highergrade/app.db` on the VM.

## Optional environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HIGHERGRADE_ADMIN_PASSCODE` | `HigherGrade Tutoring` | Admin panel passcode |
| `HIGHERGRADE_DB` | `./dev.db` (local) | Path to the SQLite database |
| `PORT` | `3000` | Port for the Express dev server |

To override, prefix the `npm run flask` command:

```bash
HIGHERGRADE_ADMIN_PASSCODE=mypassword npm start
```

## Deployment

See [math_camp/deploy/DEPLOY.md](math_camp/deploy/DEPLOY.md) for full production deployment instructions (Oracle Cloud VM + Caddy + systemd).
