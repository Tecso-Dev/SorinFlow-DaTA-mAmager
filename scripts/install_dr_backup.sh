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
NAMESPACE=sorinflow

DDIR="${DR_DATA_DIR:-$(ls -d /var/lib/rancher/k3s/storage/*_"${NAMESPACE}"_data-pvc 2>/dev/null | head -1 || true)}"

mkdir -p "$UNIT_DIR"
CHANGED=0

render_and_install() {
  local src="$1" dst="$2"
  local tmp
  tmp="$(mktemp)"
  sed -e "s#@@REPO@@#${REPO_DIR}#g" -e "s#@@DATA_PVC@@#${DDIR}#g" "$src" > "$tmp"
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

echo "disaster-recovery backup units (repo: $REPO_DIR)"
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
