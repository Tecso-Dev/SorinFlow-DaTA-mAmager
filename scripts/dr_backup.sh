#!/usr/bin/env bash
#
# Disaster-recovery backup — runs on the HOST as root, nightly via
# deploy/systemd/dr-backup.timer (04:00 Asia/Tehran) or on demand via
# deploy/systemd/dr-backup.path (data/dr-request, the panel's «همین حالا»).
#
# Builds exactly the bundle scripts/new_server.sh restores from — pg_dump +
# pg_dumpall --globals-only + a per-table row-count file, the whole
# sorinflow-secrets Secret as an env file, the data-pvc (without listing
# photos or Chromium caches, with avatars/cookies/browser profiles), and
# Traefik's acme.json — encrypts it with DR_BACKUP_PASSPHRASE, splits it into
# 45 MB parts with a sha256 manifest, and hands the parts to the pod's
# `python -m app.services.dr_backup ship <dir>`, which sends them to
# Telegram (direct first, relay/proxy after — TELEGRAM_DIRECT_FIRST).
#
# DR_BACKUP_PASSPHRASE lives ONLY in the sorinflow-secrets Secret (synced
# from the DR_BACKUP_PASSPHRASE GitHub secret by deploy.yml) — this script
# reads it at run time instead of keeping a second copy on the host.
#
# Idempotent-ish: every run works in its own $STAMP directory, so a rerun
# after a failure does not collide with a half-finished one.
set -euo pipefail
umask 077   # everything this script creates (dumps, the secrets env file,
            # the passphrase) is unreadable to anyone but root by default.

NAMESPACE=sorinflow
SECRET_NAME=sorinflow-secrets
PG_POD=postgres-0
BACKEND=deploy/backend
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

# Overridable so the round-trip test can point this at a fixture tree
# instead of real k3s storage paths.
DDIR="${DR_DATA_DIR:-$(ls -d /var/lib/rancher/k3s/storage/*_"${NAMESPACE}"_data-pvc 2>/dev/null | head -1 || true)}"
TDIR="${DR_TRAEFIK_DIR:-$(ls -d /var/lib/rancher/k3s/storage/*_kube-system_traefik 2>/dev/null | head -1 || true)}"

STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d "${DR_WORK_DIR:-/root}/.sorinflow-dr-work.XXXXXX" 2>/dev/null || mktemp -d)"
BUNDLE="$WORK/bundle"
PASS_FILE=""
STAGE="راه‌اندازی"

cleanup() { rm -rf "$WORK"; [ -n "$PASS_FILE" ] && rm -f "$PASS_FILE"; }
trap cleanup EXIT

fail() {
  trap - ERR
  local msg="${1:-$STAGE}"
  echo "dr_backup: FAILED at [$STAGE]: $msg" >&2
  kubectl -n "$NAMESPACE" exec "$BACKEND" -- python -m app.services.dr_backup alert \
    "🛑 بکاپ فاجعهٔ سورین‌فلو شکست خورد — مرحله: $STAGE ($msg)" >/dev/null 2>&1 || true
  exit 1
}
trap 'fail "خطای غیرمنتظره"' ERR

[ -n "$DDIR" ] || fail "پوشهٔ data-pvc روی هاست پیدا نشد"
mkdir -p "$BUNDLE/db" "$BUNDLE/k8s"

# A leftover request from a previous run (or one dropped while this run was
# already building a bundle) must not fire the .path unit again the moment
# this run finishes — it is consumed here, once, by the run it triggered.
rm -f "$DDIR/dr-request" 2>/dev/null || true

# ── 1. the passphrase, from the Secret, no second copy on the host ─────────
STAGE="گرفتن DR_BACKUP_PASSPHRASE از Secret"
PASSPHRASE="$(kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" \
  -o jsonpath='{.data.DR_BACKUP_PASSPHRASE}' 2>/dev/null | base64 -d 2>/dev/null || true)"
[ -n "$PASSPHRASE" ] || fail "DR_BACKUP_PASSPHRASE در Secret خالی است — ابتدا در GitHub Secrets تنظیم و دیپلوی کنید"
PASS_FILE="$(mktemp)"
chmod 600 "$PASS_FILE"
printf '%s' "$PASSPHRASE" > "$PASS_FILE"
unset PASSPHRASE

# ── 2. the whole Secret, as the env file new_server.sh restores from ───────
STAGE="خواندن Secret سرور"
kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" -o json \
  | jq -r '.data | to_entries[] | "\(.key)=\(.value | @base64d)"' \
  > "$BUNDLE/k8s/sorinflow-secrets.env"
chmod 600 "$BUNDLE/k8s/sorinflow-secrets.env"

DBU="$(grep '^POSTGRES_USER=' "$BUNDLE/k8s/sorinflow-secrets.env" | cut -d= -f2-)"
DBN="$(grep '^POSTGRES_DB=' "$BUNDLE/k8s/sorinflow-secrets.env" | cut -d= -f2-)"
[ -n "$DBU" ] && [ -n "$DBN" ] || fail "POSTGRES_USER یا POSTGRES_DB در Secret نبود"

# ── 3. the database: dump, globals, per-table row counts ───────────────────
STAGE="pg_dump"
kubectl -n "$NAMESPACE" exec "$PG_POD" -- pg_dump -U "$DBU" -d "$DBN" -Fc \
  > "$BUNDLE/db/divar_scraper.dump"

STAGE="pg_dumpall --globals-only"
kubectl -n "$NAMESPACE" exec "$PG_POD" -- pg_dumpall -U "$DBU" --globals-only \
  > "$BUNDLE/db/globals.sql"

STAGE="شمارش ردیف‌های هر جدول"
: > "$BUNDLE/db/row-counts.txt"
while IFS= read -r t; do
  [ -n "$t" ] || continue
  n="$(kubectl -n "$NAMESPACE" exec "$PG_POD" -- psql -U "$DBU" -d "$DBN" -tAc "select count(*) from \"$t\"")"
  printf '%s %s\n' "$t" "$n" >> "$BUNDLE/db/row-counts.txt"
done < <(kubectl -n "$NAMESPACE" exec "$PG_POD" -- psql -U "$DBU" -d "$DBN" -tAc \
  "select tablename from pg_tables where schemaname='public' order by 1")

# ── 4. the data volume — without photos or Chromium caches ─────────────────
STAGE="آرشیو data-pvc"
shopt -s nullglob dotglob
ITEMS=()
for d in "$DDIR"/*; do
  base="$(basename "$d")"
  case "$base" in
    # downloads/ is the forwarder APK mirror, fetched again every few hours;
    # backups/ is 14 nightly JSON copies of the database the pg_dump above
    # already carries — together they would make every bundle many times
    # the size of what is irreplaceable.
    images|dr-outbox|downloads|backups|.sorinflow-dr-work.*) continue ;;
  esac
  ITEMS+=("$base")
done
# Listing photos are excluded wholesale (re-scraped, not irreplaceable); the
# avatar images are small, few and NOT re-derivable from Divar, so they are
# carried back in explicitly.
[ -d "$DDIR/images/avatars" ] && ITEMS+=("images/avatars")
if [ "${#ITEMS[@]}" -gt 0 ]; then
  tar -C "$DDIR" \
    --exclude='*/Cache' --exclude='*/Code Cache' --exclude='*/GPUCache' \
    --exclude='*/DawnCache' --exclude='*/DawnGraphiteCache' \
    --exclude='*/GrShaderCache' --exclude='*/ShaderCache' \
    --exclude='*/Service Worker/CacheStorage' --exclude='*/Service Worker/ScriptCache' \
    -cf "$BUNDLE/data-pvc.tar" "${ITEMS[@]}"
else
  tar -cf "$BUNDLE/data-pvc.tar" -T /dev/null
fi

# ── 5. Traefik's certificate, if the volume has one yet ────────────────────
if [ -n "$TDIR" ] && [ -f "$TDIR/acme.json" ]; then
  cp "$TDIR/acme.json" "$BUNDLE/k8s/traefik-acme.json"
fi

# ── 6. encrypt, split, manifest ─────────────────────────────────────────────
STAGE="رمزنگاری و تکه‌کردن"
tar -C "$BUNDLE" -cf "$WORK/plain.tar" .
gpg --batch --yes --pinentry-mode loopback --no-symkey-cache \
  --passphrase-file "$PASS_FILE" --symmetric --cipher-algo AES256 \
  -o "$WORK/enc.gpg" "$WORK/plain.tar"
rm -f "$WORK/plain.tar"

# A bundle stays in the outbox until Telegram takes it. With Telegram out
# of reach for a week, every night would add another full bundle to the
# data volume until it filled; the newest undelivered one is all a retry
# needs. (Stamps sort by time; nullglob is on from step 4.)
STALE=( "$DDIR"/dr-outbox/*/ )
for ((i = 0; i < ${#STALE[@]} - 1; i++)); do rm -rf "${STALE[i]}"; done

OUTDIR="$DDIR/dr-outbox/$STAMP"
mkdir -p "$OUTDIR"
PART_PREFIX="sorinflow-dr-$STAMP.tar.gpg.part"
# -d: numeric suffixes (readable, sorts correctly); -a4: room for up to
# 10000 parts (450GB at 45MB each) without ever needing a wider suffix.
split -d -b 45m -a 4 "$WORK/enc.gpg" "$OUTDIR/$PART_PREFIX"
rm -f "$WORK/enc.gpg"

: > "$WORK/parts.tsv"
for f in "$OUTDIR/$PART_PREFIX"[0-9][0-9][0-9][0-9]; do
  [ -e "$f" ] || continue
  name="$(basename "$f")"
  size="$(wc -c < "$f" | tr -d ' ')"
  sha="$(sha256sum "$f" | awk '{print $1}')"
  printf '%s\t%s\t%s\n' "$name" "$size" "$sha" >> "$WORK/parts.tsv"
done
[ -s "$WORK/parts.tsv" ] || fail "هیچ بخشی از بکاپ رمزشده ساخته نشد"

PARTS_JSON="$(jq -R -s -c '
  split("\n") | map(select(length>0) | split("\t")) |
  map({name: .[0], size: (.[1]|tonumber), sha256: .[2]})
' "$WORK/parts.tsv")"

jq -n --arg stamp "$STAMP" \
      --arg created_at "$(date +%Y-%m-%dT%H:%M:%S%z)" \
      --arg row_counts "$(head -c 3000 "$BUNDLE/db/row-counts.txt" 2>/dev/null || true)" \
      --argjson parts "$PARTS_JSON" \
      '{stamp: $stamp, created_at: $created_at, row_counts: $row_counts, parts: $parts}' \
      > "$OUTDIR/manifest.json"

# ── 7. ship — the pod reaches Telegram, the host cannot ────────────────────
STAGE="ارسال به تلگرام"
kubectl -n "$NAMESPACE" exec "$BACKEND" -- python -m app.services.dr_backup ship \
  "/app/data/dr-outbox/$STAMP"

echo "dr_backup: bundle $STAMP shipped"
