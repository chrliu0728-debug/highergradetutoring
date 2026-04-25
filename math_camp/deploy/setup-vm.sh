#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# One-shot provisioning script for an Oracle Cloud Ubuntu VM.
# Installs Caddy, opens the firewall, and sets up the site dir.
#
# Usage (run AS A NORMAL USER on the VM, with sudo available):
#   bash setup-vm.sh
#
# Then upload the math_camp folder contents into /var/www/highergrade
# and edit /etc/caddy/Caddyfile with your real domain.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

echo "─── Updating package index ───"
sudo apt-get update -y

echo "─── Installing Caddy ───"
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list

sudo apt-get update -y
sudo apt-get install -y caddy

echo "─── Opening ports 80 + 443 in the OS firewall ───"
# Oracle's Ubuntu image uses iptables-persistent. Ensure 80/443 in.
sudo iptables -I INPUT 1 -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
# Save iptables rules across reboots
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save || true

echo "─── Creating site directory ───"
sudo mkdir -p /var/www/highergrade
sudo chown -R "$USER":"$USER" /var/www/highergrade
sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy

echo
echo "✅ VM provisioning complete."
echo
echo "Next steps:"
echo "  1. Upload your math_camp/* files into /var/www/highergrade/"
echo "       (rsync / scp / git clone — whatever you prefer)"
echo "  2. Replace /etc/caddy/Caddyfile with the one from this deploy/ folder,"
echo "     editing REPLACE_WITH_YOUR_DOMAIN.ca to your real domain."
echo "  3. sudo systemctl reload caddy"
echo "  4. Wait ~30s for Let's Encrypt cert issuance, then visit https://yourdomain.ca"
echo
echo "Firewall reminder: in the Oracle Cloud console, also add Ingress Rules"
echo "to your VCN's default Security List for TCP 80 + 443 (source 0.0.0.0/0)."
