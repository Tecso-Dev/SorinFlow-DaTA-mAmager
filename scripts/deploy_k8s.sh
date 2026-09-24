#!/usr/bin/env bash
#
# Render one k8s/overlays/* with kustomize and roll it out. All the rollout
# logic lives here rather than in deploy.yml/staging.yml so both workflows
# (and a person, locally) run exactly the same sequence against whatever
# cluster their kubeconfig points at.
#
#   IMAGE=ghcr.io/tecso-dev/sorinflow-data-manager:<sha> \
#   OVERLAY=production \
#   SECRETS_HASH=<from the "sync secrets" step, or unset> \
#   KUBECONFIG=/etc/rancher/k3s/k3s.yaml \
#     scripts/deploy_k8s.sh
#
# Order, and why:
#   1. namespace/config/PVCs/postgres/redis/services — nothing here depends
#      on the app image, and postgres+redis must be answering before
#      anything tries to migrate or connect.
#   2. the migrate Job — before any app pod exists on the new image, so
#      nothing ever serves a request against a schema it does not expect.
#   3. the data-ownership Job — see k8s/base/ownership-job.yaml. Only scales
#      the app to 0 first on the ONE deploy that is still moving off the old
#      root image; every deploy after that runs it with everything up, and
#      the find inside it is then a fast no-op.
#   4. Deployments/Services/Ingress — the actual rollout.
#   5. NetworkPolicies, last, then a live check from inside a backend pod
#      that they did not lock out something real (DNS, Redis, egress). A
#      NetworkPolicy is the one kind of change here that can look perfectly
#      healthy in `kubectl get` and still have broken the app's ability to
#      talk to anything.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${IMAGE:?set IMAGE to the full image ref this deploy rolls out, e.g. ghcr.io/tecso-dev/sorinflow-data-manager:abc1234}"
: "${OVERLAY:?set OVERLAY to production or staging}"
: "${KUBECONFIG:?set KUBECONFIG to the cluster to deploy to}"
SECRETS_HASH="${SECRETS_HASH:-unsynced}"
export KUBECONFIG

case "$OVERLAY" in
  production|staging) ;;
  *) echo "::error::OVERLAY must be production or staging, got: $OVERLAY" >&2; exit 1 ;;
esac
case "$IMAGE" in
  *:latest) echo "::error::IMAGE must be a specific tag, not :latest — refusing to deploy a moving target." >&2; exit 1 ;;
esac

OVERLAY_DIR="k8s/overlays/$OVERLAY"
PLACEHOLDER_IMAGE="ghcr.io/tecso-dev/sorinflow-data-manager:latest"
SHORT_SHA="${IMAGE##*:}"
MIGRATE_NAME="migrate-${SHORT_SHA}"

NS="$(sed -n 's/^namespace: //p' "$OVERLAY_DIR/kustomization.yaml" | head -1)"
[ -n "$NS" ] || { echo "::error::could not read 'namespace:' from $OVERLAY_DIR/kustomization.yaml" >&2; exit 1; }

say()  { echo; echo "── [$(date +%H:%M:%S)] $*"; }
# Retried once — the same reasoning as everywhere else this pattern is used
# (deploy.yml, new_server.sh): a single network hiccup to the node's API
# server should not fail an otherwise-fine deploy.
retry_kubectl() { "$@" || { sleep 10; "$@"; }; }

TMP_PREFIX="$(mktemp -d)/sorinflow-deploy"
trap 'rm -rf "$(dirname "$TMP_PREFIX")"' EXIT

# Prints stdin, keeping only whole "---"-separated YAML documents that
# contain a line equal to $1 once leading/trailing whitespace is stripped —
# e.g. "kind: Job" or "app: migrate". Used instead of a real YAML tool
# because kustomize's own output is regular enough for this to be exact: a
# document's top-level fields are never indented, so a line can only ever
# equal "kind: X" when it really is that document's kind.
select_doc() {
  awk -v needle="$1" '
    function flush() { if (doc != "" && found) printf "---\n%s", doc; doc=""; found=0 }
    /^---[[:space:]]*$/ { flush(); next }
    { line=$0; gsub(/^[ \t]+|[ \t]+$/, "", line); if (line == needle) found=1 }
    { doc = doc $0 "\n" }
    END { flush() }
  '
}
# The complement: drops any document whose "kind:" is one of the
# space-separated names in $1.
exclude_kinds() {
  awk -v excl=" $1 " '
    function flush() { if (doc != "" && !skip) printf "---\n%s", doc; doc=""; skip=0 }
    /^---[[:space:]]*$/ { flush(); next }
    /^kind:[[:space:]]/ { if (index(excl, " " $2 " ") > 0) skip=1 }
    { doc = doc $0 "\n" }
    END { flush() }
  '
}

# ── render + substitute ─────────────────────────────────────────────────────
say "rendering $OVERLAY_DIR"
RENDERED="${TMP_PREFIX}.rendered.yaml"
kubectl kustomize "$OVERLAY_DIR" > "$RENDERED"

# kustomize re-emits YAML: the annotation's quotes in base/*.yaml come out as a
# plain `synced-secrets: unsynced`, so the pattern takes it with or without them.
# sed to a new file, not -i: BSD sed (a laptop) and GNU sed (the runner)
# disagree about -i's argument, and this way works on both.
sed -E \
  -e "s|^([[:space:]]*image: )${PLACEHOLDER_IMAGE//\//\\/}\$|\\1${IMAGE//\//\\/}|" \
  -e "s/(sorinflow\.com\/synced-secrets: )\"?unsynced\"?\$/\\1\"${SECRETS_HASH}\"/" \
  -e "s/migrate-placeholder/${MIGRATE_NAME}/" \
  "$RENDERED" > "${RENDERED}.new"
mv "${RENDERED}.new" "$RENDERED"

# If the image line is ever renamed or reshaped, this silently matches
# nothing and the placeholder — :latest — would be what actually gets
# applied. Stop loudly instead of deploying the wrong thing.
grep -qF "image: ${IMAGE}" "$RENDERED" || {
  echo "::error::Could not render IMAGE into the manifests." >&2
  exit 1
}
if grep -qF "image: ${PLACEHOLDER_IMAGE}" "$RENDERED"; then
  echo "::error::The placeholder image is still present after substitution." >&2
  exit 1
fi
grep -qF "sorinflow.com/synced-secrets: \"${SECRETS_HASH}\"" "$RENDERED" || {
  echo "::error::Could not render the secrets fingerprint — a changed key would then not restart the pod that reads it." >&2
  exit 1
}

# ── 1. namespace, config, PVCs, postgres, redis, services ──────────────────
say "applying namespace, config, PVCs, postgres, redis, services"
CORE="${TMP_PREFIX}.core.yaml"
exclude_kinds "Deployment Ingress NetworkPolicy Job" < "$RENDERED" > "$CORE"
retry_kubectl kubectl apply -f "$CORE"
# Traefik's Let's Encrypt resolver: kept out of the kustomization (see the
# file's header — kustomize would move it out of kube-system, where k3s's
# helm-controller looks), so it is applied here, production only. Unchanged,
# this is a no-op and Traefik is not touched. Without it a rebuilt server
# serves no certificate, and the live one drifts from the repo.
if [ "$OVERLAY" = production ]; then
  retry_kubectl kubectl apply -f k8s/overlays/production/traefik-acme.yaml
fi

retry_kubectl kubectl -n "$NS" rollout status statefulset/postgres --timeout=300s
retry_kubectl kubectl -n "$NS" rollout status statefulset/redis --timeout=120s

# ── 2. migrate ───────────────────────────────────────────────────────────────
say "running migrations (job/${MIGRATE_NAME})"
MIGRATE_JOB="${TMP_PREFIX}.migrate.yaml"
select_doc "kind: Job" < "$RENDERED" | select_doc "app: migrate" > "$MIGRATE_JOB"
[ -s "$MIGRATE_JOB" ] || { echo "::error::could not find the migrate Job in the rendered manifests." >&2; exit 1; }

# Jobs are immutable once created; delete whatever migrate-<sha> Jobs are
# left from earlier deploys before creating this one.
retry_kubectl kubectl -n "$NS" delete job -l app=migrate --ignore-not-found
retry_kubectl kubectl apply -f "$MIGRATE_JOB"

if ! kubectl -n "$NS" wait --for=condition=complete "job/${MIGRATE_NAME}" --timeout=900s; then
  echo "::error::migrate job ${MIGRATE_NAME} did not complete within 15 minutes. Nothing else has"
  echo "::error::changed yet — api/worker/scheduler are still on the previous image. Its logs:"
  kubectl -n "$NS" logs "job/${MIGRATE_NAME}" --tail=200 || true
  exit 1
fi

# ── 3. data ownership ────────────────────────────────────────────────────────
run_ownership_job() {
  say "data-ownership fixup on data-pvc (idempotent)"
  local job="${TMP_PREFIX}.ownership.yaml"
  select_doc "app: data-ownership" < "$RENDERED" > "$job"
  [ -s "$job" ] || { echo "::error::could not find the data-ownership Job in the rendered manifests." >&2; exit 1; }
  retry_kubectl kubectl -n "$NS" delete job data-ownership --ignore-not-found
  retry_kubectl kubectl apply -f "$job"
  if ! kubectl -n "$NS" wait --for=condition=complete job/data-ownership --timeout=300s; then
    echo "::error::the data-ownership job did not complete. Its logs:"
    kubectl -n "$NS" logs job/data-ownership --tail=100 || true
    exit 1
  fi
}

EXISTING_UID=""
if kubectl -n "$NS" get deployment backend >/dev/null 2>&1; then
  EXISTING_UID="$(kubectl -n "$NS" get deployment backend -o jsonpath='{.spec.template.spec.securityContext.runAsUser}' 2>/dev/null || true)"
fi
if [ -n "$EXISTING_UID" ]; then
  say "backend already runs as uid $EXISTING_UID — running the ownership job with apps up"
  run_ownership_job
elif kubectl -n "$NS" get deployment backend >/dev/null 2>&1; then
  FIRST_TRANSITION=1
  say "FIRST TRANSITION: the live backend Deployment has no runAsUser (the old root image)."
  echo "The old image scrapes in-process AS ROOT, so it and a uid-1000 pod cannot safely"
  echo "share data-pvc at once. Scaling backend to 0 first — a short, accepted downtime"
  echo "(Sobhan's decision), not a bug."
  retry_kubectl kubectl -n "$NS" scale deployment/backend --replicas=0
  for i in $(seq 1 60); do
    left="$(kubectl -n "$NS" get pods -l app=backend --no-headers 2>/dev/null | grep -vc '^$' || true)"
    [ "${left:-0}" = "0" ] && break
    sleep 5
  done
  run_ownership_job
else
  say "backend does not exist yet (first-ever deploy to this namespace) — running the ownership job"
  run_ownership_job
fi

# ── 4. the rollout itself ───────────────────────────────────────────────────
say "applying api, worker, scheduler and the ingress"
APP="${TMP_PREFIX}.app.yaml"
{ select_doc "kind: Deployment" < "$RENDERED"; select_doc "kind: Ingress" < "$RENDERED"; } > "$APP"
retry_kubectl kubectl apply -f "$APP"

# Kept from deploy.yml: `kubectl get pods` above only ever ran when the
# rollout succeeded, which is precisely when nobody needs it — the 65048fc
# failure printed minutes of "pending termination" and not one word about why
# the new pod was unhealthy.
rollback_and_diagnose() {
  local dep="$1"
  echo "::error::$dep did not become ready — rolling back to the previous image."
  kubectl -n "$NS" rollout undo "deployment/$dep" || true
  # The first move to three processes has nothing to undo worker and scheduler
  # TO, and the image backend returns to runs every loop and every scrape in
  # its own process: left beside a live scheduler it would send every Telegram
  # message twice and fight it for getUpdates. So a failure here puts back
  # exactly what ran before — one backend pod on the previous image, worker
  # and scheduler off.
  if [ "${FIRST_TRANSITION:-0}" = 1 ]; then
    echo "::error::first move to separate processes failed — restoring the single previous pod."
    kubectl -n "$NS" scale deployment/worker deployment/scheduler --replicas=0 || true
    [ "$dep" = backend ] || kubectl -n "$NS" rollout undo deployment/backend || true
    kubectl -n "$NS" scale deployment/backend --replicas=1 || true
  fi
  echo "::group::pods"
  kubectl -n "$NS" get pods -o wide || true
  echo "::endgroup::"
  echo "::group::$dep pod description (events at the bottom)"
  local pod
  pod="$(kubectl -n "$NS" get pods -l "app=$dep" --sort-by=.metadata.creationTimestamp -o name | tail -1)"
  kubectl -n "$NS" describe "$pod" || true
  echo "::endgroup::"
  echo "::group::$dep pod logs"
  kubectl -n "$NS" logs "$pod" --tail=120 || true
  kubectl -n "$NS" logs "$pod" --previous --tail=60 2>/dev/null || true
  echo "::endgroup::"
  echo "::group::sessions holding locks (the deadlock that bit us before)"
  kubectl -n "$NS" exec postgres-0 -- psql -U sorinflow -d divar_scraper -c \
    "SELECT pid, state, wait_event_type, left(query,80) AS query,
            now()-state_change AS held
     FROM pg_stat_activity
     WHERE state <> 'idle' OR state = 'idle in transaction'
     ORDER BY state_change;" || true
  echo "::endgroup::"
  exit 1
}

kubectl -n "$NS" rollout status deployment/backend --timeout=300s || rollback_and_diagnose backend
kubectl -n "$NS" rollout status deployment/scheduler --timeout=300s || rollback_and_diagnose scheduler

# worker gets terminationGracePeriodSeconds 7200 so an in-flight scrape job
# can finish draining — `rollout status`/`kubectl wait` would sit and wait
# for that old pod to fully disappear too. All that actually matters here is
# that the NEW pod came up; the old one is left alone to drain on its own
# schedule, up to 2 hours, and nothing after this waits on it.
say "waiting for worker's new pod to become ready (the old one may keep draining for up to 2h)"
ok=0
for i in $(seq 1 60); do
  new_rs="$(kubectl -n "$NS" get rs -l app=worker --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || true)"
  if [ -n "$new_rs" ]; then
    hash="$(kubectl -n "$NS" get rs "$new_rs" -o jsonpath='{.metadata.labels.pod-template-hash}' 2>/dev/null || true)"
    if [ -n "$hash" ]; then
      ready="$(kubectl -n "$NS" get pods -l "app=worker,pod-template-hash=${hash}" -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
      [ "$ready" = "True" ] && { ok=1; break; }
    fi
  fi
  sleep 5
done
[ "$ok" = 1 ] || rollback_and_diagnose worker

kubectl -n "$NS" get pods -o wide

# ── 5. NetworkPolicies last, then prove they did not break anything real ───
say "applying NetworkPolicies"
NETPOL="${TMP_PREFIX}.netpol.yaml"
select_doc "kind: NetworkPolicy" < "$RENDERED" > "$NETPOL"
retry_kubectl kubectl apply -f "$NETPOL"

verify_fail() {
  echo "::error::$1"
  echo "::error::Deleting the NetworkPolicies just applied and stopping — traffic that worked a"
  echo "::error::minute ago must keep working, not silently break because of this deploy."
  kubectl -n "$NS" delete -f "$NETPOL" --ignore-not-found || true
  exit 1
}

say "verifying from inside a backend pod: /ready, redis, DNS, HTTPS egress"
POD="$(kubectl -n "$NS" get pods -l app=backend -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$POD" ] || verify_fail "no backend pod found to verify from"

kubectl -n "$NS" exec "$POD" -c api -- \
  sh -c 'curl -fsS --max-time 5 http://127.0.0.1:8000/ready >/dev/null' \
  || verify_fail "/ready did not answer 200 from inside the pod (postgres/DNS reachability, or the NetworkPolicy itself, is broken)"

kubectl -n "$NS" exec "$POD" -c api -- \
  python3 -c "
import asyncio
from app.database import get_redis

async def main():
    r = await get_redis()
    assert await r.ping()

asyncio.run(main())
" || verify_fail "could not PING redis from inside the pod"

kubectl -n "$NS" exec "$POD" -c api -- sh -c 'getent hosts divar.ir >/dev/null' \
  || verify_fail "DNS lookup for divar.ir failed from inside the pod"

kubectl -n "$NS" exec "$POD" -c api -- sh -c 'curl -sS -o /dev/null --max-time 10 https://divar.ir' \
  || verify_fail "HTTPS egress to divar.ir failed from inside the pod (any HTTP status back would have counted)"

say "done — $OVERLAY is on ${IMAGE}"
