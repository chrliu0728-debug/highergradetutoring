# Deploying HigherGrade Tutoring to the Internet

The math camp site is a **static frontend + Flask + SQLite backend**.
Caddy serves the HTML/CSS/JS directly and reverse-proxies `/api/*` to a
Python service running on `127.0.0.1:5000`. All persistent data
(students, classes, transactions, staff, etc.) lives in a SQLite file
at `/var/lib/highergrade/app.db`.

---

## Where you are right now

| Step | Status |
|------|--------|
| 1. Provision Oracle Cloud VM (Ubuntu 22.04, public IP attached) | ✅ done |
| 2. Open ports 80 + 443 in Oracle security list **and** OS firewall | ✅ done |
| 3. Install Caddy on the VM | ✅ done |
| 4. Configure Porkbun DNS to point at the VM | ⏳ **next** |
| 5. Install the Caddyfile + clone the GitHub repo | ⏳ next |
| 6. Verify HTTPS works | ⏳ next |

**Your current setup:**

| Resource    | Value |
|-------------|-------|
| VM IP       | `40.233.122.40` |
| SSH command | `ssh -i ~/.ssh/oracle_tutoring ubuntu@40.233.122.40` |
| Domain      | `highergradetutoring.ca` (registered at Porkbun) |
| Code repo   | <https://github.com/chrliu0728-debug/highergradetutoring> |
| Site files  | `math_camp/` subfolder of that repo |

The remaining steps are below.

---

## Step 4 — Point Porkbun DNS at the VM

Sign in at <https://porkbun.com> → **Domain Management → DNS** for
`highergradetutoring.ca`. Add these records (delete any existing parking
records first):

| Type | Host | Answer            | TTL |
|------|------|-------------------|-----|
| A    | (blank, root) | `40.233.122.40` | 600 |
| A    | `www`         | `40.233.122.40` | 600 |
| CAA (optional) | (blank) | `0 issue "letsencrypt.org"` | 600 |

The CAA record is optional but recommended — it tells the world that only
Let's Encrypt is allowed to issue TLS certs for your domain.

See [PORKBUN-DNS.md](PORKBUN-DNS.md) for the full reference and optional email
forwarding setup.

**Wait 5–15 minutes** for DNS to propagate, then verify from your laptop:

```bash
dig +short highergradetutoring.ca
dig +short www.highergradetutoring.ca
```

Both should print `40.233.122.40`. If they're empty, give it another 10 min.

## Step 5 — Install Caddyfile + clone the GitHub repo onto the VM

SSH into the VM:

```bash
ssh -i ~/.ssh/oracle_tutoring ubuntu@40.233.122.40
```

### 5a. Clone the GitHub repo into the webroot

```bash
# Make sure git is installed (it usually is on Oracle's Ubuntu image)
sudo apt-get install -y git

# Create the webroot directory
sudo mkdir -p /var/www
sudo chown ubuntu:ubuntu /var/www

# Clone the repo. The site files are in the math_camp/ subfolder.
cd /var/www
git clone https://github.com/chrliu0728-debug/highergradetutoring.git highergrade

# Confirm the site files are where Caddy expects them
ls /var/www/highergrade/math_camp/index.html
# → /var/www/highergrade/math_camp/index.html
```

### 5b. Install the Caddyfile

The Caddyfile in this `deploy/` folder is already configured for
`highergradetutoring.ca` and points at the right webroot
(`/var/www/highergrade/math_camp`). Copy it into Caddy's config directory:

```bash
sudo cp /var/www/highergrade/math_camp/deploy/Caddyfile /etc/caddy/Caddyfile

# Make sure caddy can read it and write its own logs
sudo chown root:caddy /etc/caddy/Caddyfile
sudo chmod 644 /etc/caddy/Caddyfile
sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy
```

### 5c. Reload Caddy

```bash
sudo systemctl reload caddy
```

Caddy will now contact Let's Encrypt to obtain TLS certificates for your
domain — this takes ~30 seconds the first time. Watch the log if you want:

```bash
sudo journalctl -u caddy -f --since '1 min ago'
```

You're looking for lines like:

```
{"level":"info","msg":"certificate obtained successfully","identifier":"highergradetutoring.ca"}
```

Press `Ctrl-C` to stop tailing the log.

> **If cert issuance fails**, the most common cause is that DNS hasn't
> propagated yet. Run `dig +short highergradetutoring.ca` from the VM and
> from your laptop — both must return `40.233.122.40` before Let's Encrypt
> will succeed.

## Step 6 — Verify

Open <https://highergradetutoring.ca> in your browser. You should see the
camp homepage with a green padlock 🔒.

Also try the `www` subdomain: <https://www.highergradetutoring.ca>. It
should redirect to the apex domain (or just work — both are configured).

🎉 **You're live.**

---

## Step 5d — Install the Python backend (one-time)

```bash
# 1. System packages
sudo apt-get install -y python3-venv python3-pip

# 2. Create the DB directory, owned by the gunicorn user
sudo mkdir -p /var/lib/highergrade
sudo chown ubuntu:ubuntu /var/lib/highergrade

# 3. Create a virtualenv inside the server folder and install deps
cd /var/www/highergrade/math_camp/server
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 4. Smoke-test the app once (Ctrl-C after you see "Running on http://127.0.0.1:5000")
.venv/bin/python app.py
```

## Step 5e — Install the systemd unit

```bash
sudo cp /var/www/highergrade/math_camp/deploy/highergrade-api.service \
        /etc/systemd/system/highergrade-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now highergrade-api
sudo systemctl status highergrade-api --no-pager
```

You should see `active (running)`. Logs:

```bash
sudo journalctl -u highergrade-api -f
```

> **Override the admin passcode**: `sudo systemctl edit highergrade-api`
> and add:
> ```
> [Service]
> Environment=HIGHERGRADE_ADMIN_PASSCODE=Whatever you want
> ```
> Then `sudo systemctl restart highergrade-api`.

## Step 5f — Reload Caddy so it picks up the new `/api/*` route

```bash
sudo systemctl reload caddy
curl -sS https://highergradetutoring.ca/api/health
# → {"ok":true,"ts":1714080000}
```

## Routine updates after deployment

Whenever you change the site:

1. **Push your changes to GitHub** from your laptop:
   ```bash
   git add math_camp/
   git commit -m "Describe your change"
   git push
   ```

2. **Pull on the VM and (if backend changed) restart the API**:
   ```bash
   ssh -i ~/.ssh/oracle_tutoring ubuntu@40.233.122.40 << 'EOF'
   cd /var/www/highergrade && git pull
   if git diff --name-only HEAD~1..HEAD | grep -q '^math_camp/server/'; then
     cd math_camp/server
     .venv/bin/pip install -r requirements.txt
     sudo systemctl restart highergrade-api
   fi
   EOF
   ```

For frontend-only changes, the `git pull` alone is enough — Caddy
serves the new HTML/CSS/JS immediately.

### Migrating existing browser localStorage data

If you have student data sitting in your admin browser's localStorage
from before the migration, see [MIGRATE-LOCALSTORAGE.md](MIGRATE-LOCALSTORAGE.md)
for a one-time import procedure.

### Database backups

The DB is a single file at `/var/lib/highergrade/app.db`. A safe
nightly backup cron:

```bash
# /etc/cron.daily/highergrade-db-backup
sqlite3 /var/lib/highergrade/app.db ".backup '/var/backups/highergrade-$(date +\%F).db'"
find /var/backups -name 'highergrade-*.db' -mtime +30 -delete
```

---

## Troubleshooting

**"This site can't be reached"** → DNS hasn't propagated, or the Oracle
security list / iptables hasn't been opened. Verify:
```bash
# From your laptop:
dig +short highergradetutoring.ca           # Should be 40.233.122.40
nc -vz 40.233.122.40 80                     # Should say "succeeded"
nc -vz 40.233.122.40 443                    # Should say "succeeded"
```

**"NET::ERR_CERT_AUTHORITY_INVALID"** → Caddy hasn't finished obtaining the
cert yet (or it failed). On the VM:
```bash
sudo journalctl -u caddy --since '5 min ago' | grep -i 'error\|cert'
```
Most common cause: DNS propagation lag. Wait 10 min and `sudo systemctl
reload caddy`.

**404 on every page** → Caddy is pointing at the wrong directory. Verify:
```bash
# On the VM:
cat /etc/caddy/Caddyfile | grep root
# Should show: root * /var/www/highergrade/math_camp
ls /var/www/highergrade/math_camp/index.html
# Should exist
```

**Caddy won't start** → Configuration syntax error. Check with:
```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl status caddy --no-pager
```

**Student data resets when you switch browsers** → Expected. The site uses
`localStorage`, which is per-browser. Every browser/device has its own
independent dataset. To share data across devices, you'd need a backend
(out of scope for this static deploy).

---

## File map of this `deploy/` folder

| File                       | Purpose                                           |
|----------------------------|---------------------------------------------------|
| `DEPLOY.md`                | This guide.                                       |
| `PORKBUN-DNS.md`           | Exact DNS records to set on Porkbun.              |
| `Caddyfile`                | Production Caddy config — copy to `/etc/caddy/`.  |
| `nginx-alternative.conf`   | Use this instead if you prefer nginx + certbot.   |
| `setup-vm.sh`              | One-shot VM provisioning (already used).          |
| `upload.sh`                | (Legacy) rsync-based upload — superseded by git pull. |
