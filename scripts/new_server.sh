#!/usr/bin/env bash
#
# Bring an empty Ubuntu box up as THE SorinFlow server, from a backup bundle
# taken with the block at the bottom of this file.
#
#   scp sorinflow-backup-<date>.tar root@NEW:/root/
#   ssh root@NEW 'tar -xf /root/sorinflow-backup-<date>.tar -C /root'
#   ssh root@NEW 'bash -s' < scripts/new_server.sh /root/sorinflow-backup-<date>
#
# What it does, in the order that matters:
#   1. base OS: firewall, timezone, password SSH kept ON (house rule), swap
#   2. k3s, pinned to the version the old box ran
#   3. Traefik with Let's Encrypt — and the OLD certificate dropped in first,
#      so the site answers HTTPS the moment DNS moves, no re-issue needed
#   4. the app's namespace, secrets (unchanged: same SECRET_KEY, same DB
#      password, same OTP secret — every token, phone and session keeps working)
#   5. Postgres restored BEFORE the app ever starts, so nothing races the
#      migrations; the data volume (images, Chromium profiles, Divar cookies)
#      restored the same way
#   6. the GitHub Actions runner, when a registration token is given —
#      the deploy job then pulls the image and rolls out exactly as it does
#      today. Nothing is deployed by hand.
#
# Idempotent where it can be: rerunning after a failure is safe.
set -euo pipefail

BUNDLE=${1:?usage: new_server.sh /root/sorinflow-backup-<date> [runner-registration-token]}
RUNNER_TOKEN=${2:-}
K3S_VERSION=${K3S_VERSION:-v1.36.2+k3s1}
REPO=https://github.com/Tecso-Dev/SorinFlow-DaTA-mAmager.git
SRC=/root/SorinFlow-DaTA-mAmager
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
export DEBIAN_FRONTEND=noninteractive

say() { echo; echo "── [$(date +%H:%M:%S)] $*"; }
need() { [ -e "$1" ] || { echo "missing in bundle: $1"; exit 1; }; }
need "$BUNDLE/db/divar_scraper.dump"
need "$BUNDLE/k8s/sorinflow-secrets.env"
need "$BUNDLE/data-pvc.tar"

# ── 1. base ──────────────────────────────────────────────────────────────────
say "base packages, firewall, time"
apt-get update -y -qq && apt-get install -y -qq ufw curl git jq python3 >/dev/null
timedatectl set-timezone Asia/Tehran || true
for p in 22 80 443 6443; do ufw allow "$p/tcp" >/dev/null; done
ufw --force enable >/dev/null
# Password login stays on. Cloud images ship a drop-in that turns it off.
for f in /etc/ssh/sshd_config.d/*.conf; do
  [ -f "$f" ] && sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' "$f"
done
grep -q '^PasswordAuthentication yes' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
grep -q '^PermitRootLogin yes' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
systemctl restart ssh 2>/dev/null || systemctl restart sshd

# ── 2. k3s ───────────────────────────────────────────────────────────────────
if ! command -v k3s >/dev/null; then
  say "k3s $K3S_VERSION"
  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" sh - >/dev/null
fi
until kubectl get nodes 2>/dev/null | grep -q ' Ready'; do sleep 3; done
mkdir -p /root/.kube && cp /etc/rancher/k3s/k3s.yaml /root/.kube/config && chmod 600 /root/.kube/config

# ── 3. the manifests ─────────────────────────────────────────────────────────
say "manifests from main"
if [ -d "$SRC/.git" ]; then git -C "$SRC" pull -q; else git clone -q "$REPO" "$SRC"; fi

# swap, journald cap, inotify limits, clock — the host-level setup that was
# once typed by hand and lost with the old server. Idempotent.
say "host provisioning"
bash "$SRC/scripts/provision-host.sh" | sed 's/^/   /'

say "traefik + the old certificate"
kubectl apply -f "$SRC/k8s/06-traefik-acme.yaml" >/dev/null
# Traefik restarts with persistence; wait for its volume to exist, then put
# the Let's Encrypt account + certificate from the old box in place so HTTPS
# works the moment DNS points here — no waiting on a fresh issuance.
if [ -f "$BUNDLE/k8s/traefik-acme.json" ]; then
  for i in $(seq 1 60); do
    TDIR=$(ls -d /var/lib/rancher/k3s/storage/*_kube-system_traefik 2>/dev/null | head -1 || true)
    [ -n "$TDIR" ] && break; sleep 5
  done
  if [ -n "${TDIR:-}" ]; then
    kubectl -n kube-system scale deploy traefik --replicas=0 >/dev/null
    sleep 5
    cp "$BUNDLE/k8s/traefik-acme.json" "$TDIR/acme.json"
    chown 65532:65532 "$TDIR/acme.json" && chmod 600 "$TDIR/acme.json"
    kubectl -n kube-system scale deploy traefik --replicas=1 >/dev/null
    echo "certificate restored into $TDIR"
  else
    echo "traefik volume did not appear — it will issue a fresh certificate once DNS points here"
  fi
fi

say "namespace, secrets, postgres, redis"
kubectl apply -f "$SRC/k8s/00-namespace.yaml" >/dev/null
kubectl -n sorinflow create secret generic sorinflow-secrets \
  --from-env-file="$BUNDLE/k8s/sorinflow-secrets.env" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f "$SRC/k8s/02b-postgres-init-configmap.yaml" -f "$SRC/k8s/02-postgres.yaml" -f "$SRC/k8s/03-redis.yaml" >/dev/null
kubectl -n sorinflow rollout status sts/postgres --timeout=300s >/dev/null
kubectl -n sorinflow rollout status sts/redis --timeout=120s >/dev/null
until kubectl -n sorinflow exec postgres-0 -- pg_isready -U sorinflow >/dev/null 2>&1; do sleep 2; done

# ── 4. the database, before the app exists ───────────────────────────────────
say "restoring the database"
DBU=$(grep '^POSTGRES_USER=' "$BUNDLE/k8s/sorinflow-secrets.env" | cut -d= -f2-)
DBN=$(grep '^POSTGRES_DB=' "$BUNDLE/k8s/sorinflow-secrets.env" | cut -d= -f2-)
kubectl -n sorinflow exec -i postgres-0 -- psql -q -U "$DBU" -d "$DBN" < "$BUNDLE/db/globals.sql" >/dev/null 2>&1 || true
kubectl -n sorinflow exec -i postgres-0 -- pg_restore -U "$DBU" -d "$DBN" --no-owner --clean --if-exists --exit-on-error < "$BUNDLE/db/divar_scraper.dump"
echo "rows now:"
kubectl -n sorinflow exec postgres-0 -- psql -U "$DBU" -d "$DBN" -tAc \
  "select 'users',count(*) from users union all select 'properties',count(*) from properties union all select 'cookies',count(*) from cookies union all select 'leads',count(*) from leads" | sed 's/^/   /'
echo "rows on the old box were:"; sed 's/^/   /' "$BUNDLE/db/row-counts.txt"

# ── 5. the data volume ───────────────────────────────────────────────────────
say "data volume (images, profiles, cookies)"
# The volume is created when a backend pod is first scheduled. The image is
# private and not pulled yet, so that pod cannot start — which is fine: the
# volume gets provisioned all the same, and the pod is scaled away before
# anything could write to it.
kubectl apply -f "$SRC/k8s/04-backend.yaml" >/dev/null
for i in $(seq 1 60); do
  DDIR=$(ls -d /var/lib/rancher/k3s/storage/*_sorinflow_data-pvc 2>/dev/null | head -1 || true)
  [ -n "$DDIR" ] && break; sleep 5
done
[ -n "${DDIR:-}" ] || { echo "data volume never appeared"; exit 1; }
kubectl -n sorinflow scale deploy backend --replicas=0 >/dev/null
tar -xf "$BUNDLE/data-pvc.tar" -C "$DDIR"
echo "restored into $DDIR:"; du -sh "$DDIR"/* | sed 's/^/   /'
kubectl apply -f "$SRC/k8s/05-ingress.yaml" >/dev/null

# ── 6. the runner — the deploy job does the rest ─────────────────────────────
if [ -n "$RUNNER_TOKEN" ]; then
  say "GitHub Actions runner"
  # Pinned, like the old box's setup_runner.sh — api.github.com is not a
  # reliable thing to ask from here. The runner self-updates afterwards.
  RUNNER_VERSION=${RUNNER_VERSION:-2.337.0}
  mkdir -p /opt/actions-runner && cd /opt/actions-runner
  if [ ! -f ./config.sh ]; then
    curl -fsSL "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" | tar -xz
    ./bin/installdependencies.sh >/tmp/runner-deps.log 2>&1 || { tail -20 /tmp/runner-deps.log; exit 1; }
  fi
  export RUNNER_ALLOW_RUNASROOT=1
  # Same name as the old box, --replace: GitHub hands the registration to
  # this machine and the old runner stops receiving jobs — so a deploy can
  # never land on the box being retired.
  ./config.sh --unattended --replace \
    --url https://github.com/Tecso-Dev/SorinFlow-DaTA-mAmager \
    --token "$RUNNER_TOKEN" --name sorinflow-server --labels sorinflow --work _work >/tmp/runner-config.log 2>&1 \
    || { tail -20 /tmp/runner-config.log; exit 1; }
  ./svc.sh install root >/dev/null && ./svc.sh start >/dev/null
  cd /root
  echo "runner registered as sorinflow-server (replacing the old box's)"
fi

say "disaster-recovery backup timer"
bash "$SRC/scripts/install_dr_backup.sh" | sed 's/^/   /'

say "DONE — next: stop the OLD runner, dispatch the deploy workflow, point DNS here"
kubectl get pods -A -o wide

# ── how the bundle was made (on the old box) ─────────────────────────────────
# pg_dump -U sorinflow -d divar_scraper -Fc > db/divar_scraper.dump
# pg_dumpall -U sorinflow --globals-only > db/globals.sql
# tar -C /var/lib/rancher/k3s/storage/<data-pvc> -cf data-pvc.tar .
# kubectl get secret sorinflow-secrets -o json | decode → k8s/sorinflow-secrets.env
# cp /var/lib/rancher/k3s/storage/<traefik-pvc>/acme.json k8s/traefik-acme.json
