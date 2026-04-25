#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Upload the math_camp site to your Oracle Cloud VM via rsync.
# Run from your LOCAL machine, in the math_camp/ folder.
#
# Usage:
#   bash deploy/upload.sh ubuntu@YOUR.VM.IP
#   bash deploy/upload.sh ubuntu@YOUR.VM.IP /path/to/your/key.pem
# ──────────────────────────────────────────────────────────────
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <user@host> [path/to/ssh-key.pem]"
  exit 1
fi

REMOTE="$1"
KEY="${2:-}"
SSH_OPTS=""
if [ -n "$KEY" ]; then
  SSH_OPTS="-e ssh -i $KEY -o StrictHostKeyChecking=accept-new"
else
  SSH_OPTS="-e ssh -o StrictHostKeyChecking=accept-new"
fi

# Files we deploy. Excludes the deploy/ folder itself + any local-only stuff.
rsync -avz --delete \
  --exclude '/deploy' \
  --exclude '.DS_Store' \
  --exclude '*.swp' \
  $SSH_OPTS \
  ./ "$REMOTE:/var/www/highergrade/"

echo
echo "✅ Upload complete."
echo "Make sure the VM has Caddy running: ssh $REMOTE 'sudo systemctl status caddy --no-pager'"
