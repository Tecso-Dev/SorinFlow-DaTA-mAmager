#!/usr/bin/env bash
#
# Idempotent install of the disaster-recovery backup timer + on-demand
# watcher. Called at the end of scripts/new_server.sh (a fresh box) and from
# deploy.yml's deploy job (every deploy, so a unit file change reaches the
# server without anyone SSHing in by hand).
#
# Rerunning changes nothing: each unit is only (re)written when its rendered
# content differs from what is already installed, and systemd is reloaded
# only when that happened.
#
# usage: scripts/install_dr_backup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
UNIT_DIR="${DR_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
# The unit runs a copy here, not the checkout it was installed from: that is
# the runner's workspace, which the next job re-checks-out — the 04:00 run
# would execute whatever happened to be checked out then, as root.
BIN_DIR="${DR_BIN_DIR:-/opt/sorinflow-dr}"
NAMESPACE=sorinflow

# dr_backup.sh needs these on the host; new_server.sh installs jq, an older
# box may not have it. Better to learn it now than at 04:00.
UPDATED=0
for tool in jq gpg split sha256sum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "  $tool missing — installing"
    [ "$UPDATED" = 1 ] || { apt-get update -qq >/dev/null; UPDATED=1; }
    apt-get install -y -qq "$( [ "$tool" = gpg ] && echo gnupg || echo "$tool" )" >/dev/null
  fi
done

STORAGE=/var/lib/rancher/k3s/storage
DDIR="${DR_DATA_DIR:-$(ls -d "$STORAGE"/*_"${NAMESPACE}"_data-pvc 2>/dev/null | head -1 || true)}"

mkdir -p "$UNIT_DIR" "$BIN_DIR"
CHANGED=0

if ! cmp -s "$REPO_DIR/scripts/dr_backup.sh" "$BIN_DIR/dr_backup.sh" 2>/dev/null; then
  install -m 0700 "$REPO_DIR/scripts/dr_backup.sh" "$BIN_DIR/dr_backup.sh"
  echo "  installed: $BIN_DIR/dr_backup.sh"
fi

render_and_install() {
  local src="$1" dst="$2"
  local tmp
  tmp="$(mktemp)"
  sed -e "s#@@BIN@@#${BIN_DIR}#g" -e "s#@@DATA_PVC@@#${DDIR}#g" \
      -e "s#@@DATA_RW@@#${DDIR:-$STORAGE}#g" "$src" > "$tmp"
  if [ -f "$dst" ] && cmp -s "$tmp" "$dst"; then
    echo "  unchanged: $dst"
    rm -f "$tmp"
    return
  fi
  mv "$tmp" "$dst"
  chmod 644 "$dst"
  echo "  installed: $dst"
  CHANGED=1
}

echo "disaster-recovery backup units (from $REPO_DIR, running $BIN_DIR/dr_backup.sh)"
render_and_install "$REPO_DIR/deploy/systemd/dr-backup.service" "$UNIT_DIR/dr-backup.service"
render_and_install "$REPO_DIR/deploy/systemd/dr-backup.timer" "$UNIT_DIR/dr-backup.timer"

PATH_UNIT_INSTALLED=0
if [ -n "$DDIR" ]; then
  render_and_install "$REPO_DIR/deploy/systemd/dr-backup.path" "$UNIT_DIR/dr-backup.path"
  PATH_UNIT_INSTALLED=1
else
  echo "  data-pvc not found yet — skipping the «همین حالا» watcher (dr-backup.path);"
  echo "  the nightly timer is still installed. Rerun this script once the volume exists."
fi

if [ "$CHANGED" -eq 1 ]; then
  systemctl daemon-reload
  echo "  systemd reloaded"
fi

systemctl enable --now dr-backup.timer >/dev/null
[ "$PATH_UNIT_INSTALLED" -eq 1 ] && systemctl enable --now dr-backup.path >/dev/null

echo "done — next timed run: $(systemctl list-timers dr-backup.timer --no-legend 2>/dev/null | awk '{print $1, $2, $3}')"
