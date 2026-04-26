#!/bin/bash
# HigherGrade Tutoring — nightly DB backup → private GitHub repo
#
# Installed at /usr/local/bin/highergrade-backup-db.sh on the VM.
# Triggered by cron at 03:15 UTC daily.
#
# Prerequisites (one-time setup, see deploy/DEPLOY.md):
#   1. Private backup repo on GitHub: chrliu0728-debug/highergrade-backups
#   2. SSH deploy key (with WRITE access) at /home/ubuntu/.ssh/highergrade_backups
#   3. SSH config alias `github-backups` pointing at github.com w/ that key
#   4. Repo cloned to /var/lib/highergrade/backups
#   5. sqlite3 + cron packages installed
#
# Restore:
#   git clone git@github.com:chrliu0728-debug/highergrade-backups.git
#   cp highergrade-backups/latest/app.db /var/lib/highergrade/app.db
#   sudo systemctl restart highergrade-api

set -e

DB=/var/lib/highergrade/app.db
REPO=/var/lib/highergrade/backups
DATE=$(date +%F)         # e.g. 2026-04-26
TS=$(date +%F_%H%M%S)
DAILY=$REPO/daily/highergrade-$DATE.db
LATEST=$REPO/latest/app.db

mkdir -p "$REPO/daily" "$REPO/latest"

# `sqlite3 .backup` produces a consistent snapshot even while the API is writing
sqlite3 "$DB" ".backup '$DAILY'"
cp -f "$DAILY" "$LATEST"

# Prune daily dumps older than 60 days (keeps the repo from growing forever)
find "$REPO/daily" -name 'highergrade-*.db' -mtime +60 -delete

cd "$REPO"
git add -A
if git diff --cached --quiet; then
  echo "[$TS] No changes to commit."
  exit 0
fi
git commit -m "Backup $DATE" --quiet
GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/highergrade_backups -o IdentitiesOnly=yes" \
  git push origin HEAD:main --quiet
echo "[$TS] Backup pushed."
