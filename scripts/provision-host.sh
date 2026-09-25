#!/usr/bin/env bash
#
# Host-level setup for a SorinFlow node.
#
# Everything in here was, at some point, typed into a live server by hand and
# would have been lost the moment that server was replaced. That is the whole
# reason the file exists: the Kubernetes manifests describe the workloads, and
# nothing described the machine underneath them.
#
# Idempotent by design — safe to re-run on an existing node, and safe to run
# first on a new one. It changes no data and starts no application; it only
# makes the host a reasonable place for the manifests to land.
#
#   usage:  ./scripts/provision-host.sh          apply
#           ./scripts/provision-host.sh --check  report only, change nothing
#
# Assumes Ubuntu with k3s. Run as root.

set -euo pipefail

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
did()  { printf '   \033[32m+\033[0m %s\n' "$*"; }
skip() { printf '   \033[90m·\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root" >&2
  exit 1
fi

# ── what this machine actually has ──────────────────────────────────────────
say "Node"
MEM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
CPUS=$(nproc)
info "RAM ${MEM_MB}MB · ${CPUS} vCPU · $(. /etc/os-release && echo "$PRETTY_NAME")"

# The manifests' memory budget is sized for a ~8GB node. On a smaller one the
# backend's limit will not fit beside Postgres, Redis and k3s itself; on a
# larger one it is leaving capacity unused. Either way, say so rather than
# letting the numbers silently stop matching the machine.
if [ "$MEM_MB" -lt 7000 ]; then
  info "WARNING: k8s/base/{backend,worker,scheduler}.yaml assume ~8GB (see the"
  info "         budget comment in backend.yaml). Lower worker's limit"
  info "         (currently 3072Mi, the biggest single share) before applying"
  info "         the manifests here."
elif [ "$MEM_MB" -gt 12000 ]; then
  info "NOTE: this node is larger than the manifests assume — worker's limit"
  info "      (3072Mi) can be raised first; it holds Chromium. Measure first:"
  info "      free -m; kubectl top pod -n sorinflow"
fi

# ── journald ────────────────────────────────────────────────────────────────
#
# Found at 192MB on disk and 92MB resident on a 4GB box, which is a lot of
# memory to spend remembering that the scraper started. Capped, not disabled:
# the logs are how most of this system's bugs were found.
say "systemd-journald size cap"
JCONF=/etc/systemd/journald.conf.d/99-sorinflow.conf
WANT=$'[Journal]\nSystemMaxUse=100M\nRuntimeMaxUse=32M\n'
if [ -f "$JCONF" ] && [ "$(cat "$JCONF")" = "$WANT" ]; then
  skip "already capped ($(journalctl --disk-usage 2>/dev/null | sed 's/^.*take up //'))"
elif [ "$CHECK_ONLY" = 1 ]; then
  info "WOULD cap the journal to 100M (currently $(journalctl --disk-usage 2>/dev/null | sed 's/^.*take up //'))"
else
  mkdir -p "$(dirname "$JCONF")"
  printf '%s' "$WANT" > "$JCONF"
  journalctl --vacuum-size=100M >/dev/null 2>&1 || true
  systemctl restart systemd-journald
  did "capped to 100M — now $(journalctl --disk-usage 2>/dev/null | sed 's/^.*take up //')"
fi

# ── swap ────────────────────────────────────────────────────────────────────
#
# Kubernetes will not let a pod swap, but the host still can, and a node with
# swap degrades under memory pressure instead of having the OOM killer choose a
# victim for it. Only reported: creating swap is a disk-layout decision and not
# something a provisioning script should make on somebody's behalf.
say "Swap"
SWAP_MB=$(awk '/SwapTotal/ {printf "%d", $2/1024}' /proc/meminfo)
if [ "$SWAP_MB" -lt 512 ]; then
  info "none configured (${SWAP_MB}MB). Consider 2-4GB: it is the difference"
  info "between the node slowing down and the kernel killing something."
else
  skip "${SWAP_MB}MB present"
fi

# ── inotify ─────────────────────────────────────────────────────────────────
#
# k3s, containerd and every watching client consume inotify instances. The
# stock limits are low enough that a busy node starts refusing watches, which
# surfaces as pods that will not start and logs that stop following — with no
# error naming the real cause.
say "inotify limits"
SYSCTL=/etc/sysctl.d/99-sorinflow.conf
need_sysctl=0
for kv in "fs.inotify.max_user_instances=1024" "fs.inotify.max_user_watches=524288"; do
  k=${kv%%=*}; want=${kv##*=}
  have=$(sysctl -n "$k" 2>/dev/null || echo 0)
  if [ "$have" -lt "$want" ]; then need_sysctl=1; info "$k is $have, want $want"; fi
done
if [ "$need_sysctl" = 0 ]; then
  skip "already sufficient"
elif [ "$CHECK_ONLY" = 1 ]; then
  info "WOULD raise them in $SYSCTL"
else
  printf 'fs.inotify.max_user_instances=1024\nfs.inotify.max_user_watches=524288\n' > "$SYSCTL"
  sysctl -p "$SYSCTL" >/dev/null
  did "raised"
fi

# ── time ────────────────────────────────────────────────────────────────────
#
# Verification codes, session expiry and the scraper's date filters all compare
# timestamps. A node whose clock has drifted expires codes early and scrapes the
# wrong day, and neither failure names the clock.
say "Clock"
if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
  skip "NTP synchronised · $(timedatectl show -p Timezone --value)"
else
  info "NTP is NOT synchronised — verification codes and date-mode scrapes"
  info "will misbehave in ways that do not mention the clock."
  [ "$CHECK_ONLY" = 1 ] || { timedatectl set-ntp true && did "enabled NTP"; }
fi

# ── what the manifests still need ───────────────────────────────────────────
say "Next"
cat <<'EOF'
   This script prepares the host only. To bring the application up:

     kubectl apply -f k8s/overlays/production/namespace.yaml
     kubectl create secret generic sorinflow-secrets -n sorinflow \
       --from-env-file=.env.production      # see SECRETS.md
     kubectl apply -f k8s/overlays/production/traefik-acme.yaml
     IMAGE=ghcr.io/tecso-dev/sorinflow-data-manager:<sha> \
       OVERLAY=production KUBECONFIG=/etc/rancher/k3s/k3s.yaml \
       bash scripts/deploy_k8s.sh

   deploy.yml runs scripts/deploy_k8s.sh on every push to main. It does NOT
   create the namespace, the Secret or the Traefik ACME config — those are
   one-time, and on a new server they are yours to run (see also
   scripts/new_server.sh, which does all of this from a backup bundle).
EOF
