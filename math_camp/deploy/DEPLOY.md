# Deploying HigherGrade Tutoring to the Internet

The math camp site is **fully static** (HTML / CSS / JS — every bit of state
lives in the visitor's `localStorage`), so deployment is just "serve these files
over HTTPS at your domain." This guide gets you there in ~30 minutes on Oracle
Cloud's Always-Free tier with a Porkbun .ca domain.

---

## TL;DR — what you'll do

1. Create an Oracle Cloud VM (Always Free)
2. Open ports 80 + 443 in two places (Oracle's security list + the OS firewall)
3. Install Caddy on the VM (handles HTTPS automatically)
4. Buy a domain at Porkbun and point its A record at your VM's public IP
5. Edit one Caddyfile line with your domain, reload Caddy
6. Upload the site files via `rsync`

---

## Step 1 — Provision the Oracle Cloud VM

1. Sign up at <https://www.oracle.com/cloud/free/> (you'll need a credit card for
   identity verification — they don't charge for Always-Free resources).
2. In the Oracle Cloud console go to **Compute → Instances → Create instance**.
3. Pick **Image: Canonical Ubuntu 22.04** (or 24.04).
4. **Shape**: pick `VM.Standard.A1.Flex` (Ampere ARM, Always Free) with 1 OCPU,
   6 GB RAM. If A1 is "out of capacity" in your region, use the smaller
   `VM.Standard.E2.1.Micro` x86 shape — also free.
5. Under **Networking**, accept the defaults — Oracle creates a VCN + public
   subnet for you. Make sure **Assign a public IPv4 address** is enabled.
6. **Add SSH keys**: paste your public key (`~/.ssh/id_ed25519.pub`) or upload
   the key file. Save the matching private key locally — you'll need it.
7. Click **Create**. Wait ~1 minute for the instance to provision.
8. Copy the **Public IPv4 address** that appears on the instance page.
9. SSH in to confirm it works:
   ```bash
   ssh ubuntu@<your-public-ip>
   ```

## Step 2 — Open ports 80 + 443 (the part that trips everyone up)

> **Why two firewalls?** Oracle Cloud has a network-level firewall called a
> **security list** that runs *outside* your VM — packets blocked here never
> even reach the machine. Then Ubuntu has its own **OS firewall** (`iptables`)
> running inside the VM, and Oracle's Ubuntu image ships with that firewall
> set to drop almost everything by default. **Both** must allow TCP 80 and 443
> or your site will be unreachable.

### A — Oracle's security list (cloud-side firewall)

You're going to add two ingress rules to the security list attached to the
subnet your VM lives in.

1. **Open the Oracle Cloud console** at <https://cloud.oracle.com>. Sign in
   with the account that owns the VM.
2. **Find your VM's subnet**:
   - Click the hamburger menu (top-left) → **Compute → Instances**
   - Click your instance name (e.g. `instance-2026-04-25`)
   - Scroll down to the **Primary VNIC** card. Find the line that says
     **Subnet**: it'll be a clickable link like `Public Subnet-xyz (regional)`.
     Click it.
3. **Open the security list**:
   - On the subnet page, scroll down to the **Security Lists** card.
     Usually there's just one, called **Default Security List for vcn-xyz**.
     Click it.
4. **Add the HTTP rule (port 80)**:
   - Click the **Ingress Rules** tab.
   - Click **+ Add Ingress Rules**.
   - Fill in:
     - **Stateless?** Leave unchecked.
     - **Source Type**: `CIDR`
     - **Source CIDR**: `0.0.0.0/0`  (anywhere on the internet)
     - **IP Protocol**: `TCP`
     - **Source Port Range**: leave blank (= all)
     - **Destination Port Range**: `80`
     - **Description**: `HTTP for Caddy/Let's Encrypt`
   - Click **Add Ingress Rules**.
5. **Add the HTTPS rule (port 443)**:
   - Repeat the same flow, but with **Destination Port Range: `443`** and
     description `HTTPS for the camp site`.
6. **Verify** the Ingress Rules tab now shows your two new rules in green,
   in addition to whatever was there before (the default `22 SSH` rule).

> ✋ **If you don't see a Security List**, your VCN might be using
> *Network Security Groups* (NSGs) instead. To check: instance page →
> Primary VNIC card → look at the **Network Security Groups** field.
> If an NSG is attached, click it and add the same two ingress rules there.
> Functionally identical; the menus look the same.

### B — The VM's OS firewall (`iptables`)

This is the firewall that runs *inside* Ubuntu. The Oracle Ubuntu image
applies a strict default ruleset. We need to insert two ACCEPT rules ahead
of the default REJECT and save them so they survive reboots.

The `setup-vm.sh` script in this `deploy/` folder does all of this for you
automatically. If you've already run it, **you can skip this section**.

#### Doing it manually

SSH into the VM:

```bash
ssh ubuntu@<your-public-ip>
```

**Step 1 — Inspect the current rules** so you can confirm the change later:

```bash
sudo iptables -L INPUT -n --line-numbers
```

You'll see something like:

```
Chain INPUT (policy ACCEPT)
num  target     prot opt source         destination
1    ts-input   all  --  0.0.0.0/0      0.0.0.0/0
2    ACCEPT     all  --  0.0.0.0/0      0.0.0.0/0    state RELATED,ESTABLISHED
3    ACCEPT     icmp --  0.0.0.0/0      0.0.0.0/0
4    ACCEPT     all  --  0.0.0.0/0      0.0.0.0/0
5    ACCEPT     udp  --  0.0.0.0/0      0.0.0.0/0    udp dpt:68
6    ACCEPT     tcp  --  0.0.0.0/0      0.0.0.0/0    state NEW tcp dpt:22
7    REJECT     all  --  0.0.0.0/0      0.0.0.0/0    reject-with icmp-host-prohibited
```

That **REJECT at the bottom** is exactly what's blocking your traffic.

**Step 2 — Insert ACCEPT rules for 80 and 443** at position 6 (just before
the REJECT, after the SSH rule):

```bash
sudo iptables -I INPUT 6 -p tcp --dport 80  -m state --state NEW -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 443 -m state --state NEW -j ACCEPT
```

(`-I INPUT 6` = "insert at position 6". If you'd prefer to just put them at
the very top regardless of order, use `-I INPUT 1` for both — the existing
RELATED/ESTABLISHED rule still handles return packets correctly either way.)

**Step 3 — Verify**:

```bash
sudo iptables -L INPUT -n --line-numbers
```

You should now see your `dpt:80` and `dpt:443` ACCEPT entries listed
**above** the final REJECT.

**Step 4 — Make the rules persistent across reboots**:

```bash
sudo apt-get install -y iptables-persistent
# When prompted "Save current IPv4 rules?" — say YES
# When prompted "Save current IPv6 rules?" — say YES
sudo netfilter-persistent save
```

If `iptables-persistent` is already installed, just run:

```bash
sudo netfilter-persistent save
```

**Step 5 — Optional sanity check**: from a *different* machine (your laptop),
confirm the port is reachable:

```bash
nc -vz <your-public-ip> 80
nc -vz <your-public-ip> 443
```

You should see `Connection succeeded` (or `open`). If you see `Connection
refused`, port 80/443 is reachable but no service is listening yet — that's
fine, Caddy isn't installed until Step 3. If you see `Connection timed out`,
one of the firewalls is still blocking.

#### What if my Ubuntu uses `ufw` or `nftables` instead?

Some newer images come with `ufw` enabled. Check with:

```bash
sudo ufw status
```

If it says `Status: active`, run:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

For pure `nftables` (rare on Oracle's Ubuntu image), the equivalent is:

```bash
sudo nft add rule inet filter input tcp dport 80  accept
sudo nft add rule inet filter input tcp dport 443 accept
sudo nft list ruleset | sudo tee /etc/nftables.conf > /dev/null
```

You only ever have to use **one** of: iptables, ufw, or nftables — whichever
is active on your system. The default Oracle Ubuntu 22.04 image uses
`iptables` (managed via `iptables-persistent`), so the `iptables` commands
above are usually what you want.

### Confirming both firewalls are open

After both A and B are done, run from your laptop:

```bash
curl -v http://<your-public-ip>/
```

You won't see a webpage (Caddy isn't installed yet), but you should at
least get a `Connection refused` *quickly*, not a long hang ending in
`Connection timed out`. A fast refusal means TCP packets are reaching the
VM — both firewalls are open. A timeout means something is still blocking;
double-check both A and B above.

## Step 3 — Install Caddy on the VM

Caddy is a tiny web server that **automatically gets and renews HTTPS
certificates** from Let's Encrypt the moment your domain points at the VM. No
certbot dance required.

From your local machine, copy the setup script onto the VM and run it:

```bash
scp deploy/setup-vm.sh ubuntu@<your-public-ip>:~
ssh ubuntu@<your-public-ip>
bash setup-vm.sh
```

When it finishes, Caddy will be running and `/var/www/highergrade` will exist
and be writable by your user.

## Step 4 — Configure DNS at Porkbun (domain already registered)

The domain `highergradetutoring.ca` is already registered. Now point it at the
VM:

1. Sign in to Porkbun and go to **Domain Management → DNS** for
   `highergradetutoring.ca`.
2. Delete any default parking records.
3. Add the records listed in [PORKBUN-DNS.md](PORKBUN-DNS.md) — at minimum the
   two A records (root + `www`) pointing to your VM's public IPv4 address.
4. Wait 5–15 minutes for DNS to propagate. Verify from your local machine:
   ```bash
   dig +short highergradetutoring.ca
   dig +short www.highergradetutoring.ca
   ```
   Both should print your VM's public IP.

## Step 5 — Configure Caddy with your domain

On the VM:

```bash
# Open the Caddyfile
sudo nano /etc/caddy/Caddyfile
```

Replace its contents with the file at `deploy/Caddyfile` in this repo —
it's already configured for `highergradetutoring.ca`. Save (Ctrl-O, Enter,
Ctrl-X).

Reload Caddy:

```bash
sudo systemctl reload caddy
```

Caddy will now request a Let's Encrypt cert in the background — this takes
~30 seconds the first time. Watch the log if you're impatient:

```bash
sudo journalctl -u caddy -f
```

## Step 6 — Upload the site

From your local machine, in the math_camp folder:

```bash
bash deploy/upload.sh ubuntu@<your-public-ip>
# or with a specific SSH key:
bash deploy/upload.sh ubuntu@<your-public-ip> ~/.ssh/oracle_key.pem
```

This `rsync`s every file in `math_camp/` (except the `deploy/` folder itself)
into `/var/www/highergrade/` on the VM. Re-run any time you make changes
locally — it does an incremental sync and deletes any files you've removed.

## Step 7 — Visit your site

```
https://highergradetutoring.ca
```

If you see the camp homepage with the green padlock 🔒 — you're live.

---

## Routine updates after deployment

Every time you change a file locally and want to push it live:

```bash
bash deploy/upload.sh ubuntu@<your-public-ip>
```

That's it. Caddy serves the new file immediately (HTML/CSS/JS have a 5-min
cache so users see updates promptly).

---

## Troubleshooting

**"This site can't be reached"** → DNS hasn't propagated yet, or the Oracle
security list isn't set. Verify with `dig +short highergradetutoring.ca` and `curl -v
http://highergradetutoring.ca` from your local machine.

**HTTPS cert errors** → Caddy couldn't reach Let's Encrypt. Check the firewall
(`sudo iptables -L -n` should show `dpt:80` and `dpt:443` ACCEPT). Run
`sudo journalctl -u caddy --since '5 min ago'` to see why it's failing.

**404 on every page** → uploaded into the wrong directory. SSH in, run
`ls /var/www/highergrade/` — you should see `index.html` etc. directly there
(not in a subfolder).

**Permissions errors during upload** → `sudo chown -R ubuntu:ubuntu
/var/www/highergrade` on the VM, then re-run upload.

**The student data resets when I switch browsers** → expected. The site uses
`localStorage`, which is per-browser. Every browser/device has its own
independent dataset. To share data across devices, you'd need a backend
(out of scope for this static deploy).

---

## What about the APSim integration?

The `ap_simulator/frontend/public/math_camp/` folder was just for previewing
the math camp inside another local dev tool. **Ignore it for production.** Only
the top-level `math_camp/` folder gets deployed to the VM.

---

## File map of this `deploy/` folder

| File                       | Purpose                                           |
|----------------------------|---------------------------------------------------|
| `DEPLOY.md`                | This guide.                                       |
| `PORKBUN-DNS.md`           | Exact DNS records to set on Porkbun.              |
| `Caddyfile`                | Production Caddy config — copy to `/etc/caddy/`.  |
| `nginx-alternative.conf`   | Use this instead if you prefer nginx + certbot.   |
| `setup-vm.sh`              | One-shot VM provisioning (run on the VM).         |
| `upload.sh`                | Local helper to rsync site files to the VM.       |
