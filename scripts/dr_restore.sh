#!/usr/bin/env bash
#
# Turn a folder of downloaded DR parts back into the plaintext bundle
# scripts/new_server.sh restores from. Needs no network: every check here
# is local (sha256, gpg, tar).
#
# usage: scripts/dr_restore.sh <dir-with-parts>
#
# <dir-with-parts> holds everything pulled out of the Telegram chat for one
# backup: the numbered .part#### files, and the manifest — either as
# manifest.json (a bundle straight off the data-pvc) or as
# sorinflow-dr-<stamp>.manifest.json (the filename it has once downloaded
# from Telegram, per app/services/dr_backup.py's ship()).
set -euo pipefail

PASS_FILE=""
WORK_ENC=""
cleanup() { rm -f "$PASS_FILE" "$WORK_ENC"; }
trap cleanup EXIT

DIR="${1:?usage: dr_restore.sh <dir-with-parts>}"
[ -d "$DIR" ] || { echo "no such directory: $DIR" >&2; exit 1; }

MANIFEST="$DIR/manifest.json"
if [ ! -f "$MANIFEST" ]; then
  MANIFEST="$(ls "$DIR"/*.manifest.json 2>/dev/null | head -1 || true)"
fi
[ -n "$MANIFEST" ] && [ -f "$MANIFEST" ] || { echo "no manifest.json (or *.manifest.json) in $DIR" >&2; exit 1; }

STAMP="$(jq -r '.stamp' "$MANIFEST")"
echo "== bundle $STAMP — $(jq -r '.created_at' "$MANIFEST") =="

# ── 1. verify every part before touching anything ──────────────────────────
FAIL=0
PART_COUNT=0
while IFS=$'\t' read -r name size want_sha; do
  PART_COUNT=$((PART_COUNT + 1))
  f="$DIR/$name"
  if [ ! -f "$f" ]; then
    echo "MISSING part: $name" >&2
    FAIL=1
    continue
  fi
  got_size="$(wc -c < "$f" | tr -d ' ')"
  got_sha="$(sha256sum "$f" | awk '{print $1}')"
  if [ "$got_size" != "$size" ] || [ "$got_sha" != "$want_sha" ]; then
    echo "CHECKSUM MISMATCH: $name (refusing to restore)" >&2
    FAIL=1
  fi
done < <(jq -r '.parts[] | [.name, .size, .sha256] | @tsv' "$MANIFEST")

[ "$PART_COUNT" -gt 0 ] || { echo "manifest lists no parts" >&2; exit 1; }
[ "$FAIL" -eq 0 ] || { echo "one or more parts failed verification — nothing was restored" >&2; exit 1; }
echo "sha256 OK — $PART_COUNT part(s)"

# ── 2. the passphrase, safely ───────────────────────────────────────────────
if [ -n "${DR_BACKUP_PASSPHRASE:-}" ]; then
  PASSPHRASE="$DR_BACKUP_PASSPHRASE"
elif [ -n "${DR_PASSPHRASE_FILE:-}" ]; then
  PASSPHRASE="$(cat "$DR_PASSPHRASE_FILE")"
else
  read -r -s -p "DR_BACKUP_PASSPHRASE: " PASSPHRASE
  echo
fi
[ -n "$PASSPHRASE" ] || { echo "empty passphrase" >&2; exit 1; }

PASS_FILE="$(mktemp)"
chmod 600 "$PASS_FILE"
printf '%s' "$PASSPHRASE" > "$PASS_FILE"
unset PASSPHRASE

# ── 3. decrypt (parts back together, in manifest order) ────────────────────
OUTDIR="$DIR/restored-$STAMP"
mkdir -p "$OUTDIR"
WORK_ENC="$DIR/.sorinflow-dr-$STAMP.tar.gpg"
: > "$WORK_ENC"
while IFS= read -r name; do
  cat "$DIR/$name" >> "$WORK_ENC"
done < <(jq -r '.parts[].name' "$MANIFEST")

GPG_LOG="$(mktemp)"
GPG_STATUS="$(mktemp)"
if ! gpg --batch --yes --pinentry-mode loopback --no-symkey-cache \
     --status-file "$GPG_STATUS" \
     --passphrase-file "$PASS_FILE" --decrypt -o "$OUTDIR/.plain.tar" "$WORK_ENC" 2>"$GPG_LOG"; then
  echo "gpg could not decrypt — wrong passphrase, or the parts are corrupt:" >&2
  cat "$GPG_LOG" >&2
  rm -f "$GPG_LOG" "$GPG_STATUS" "$OUTDIR/.plain.tar"
  exit 1
fi
# gpg --decrypt also «decrypts» a message that was never encrypted
# (gpg --store), with any passphrase, and the manifest proves nothing. Anyone
# holding the bot token could post such a «newest bundle» to the chat and
# have new_server.sh restore their secrets and database. Only a bundle that
# really was encrypted — so with our passphrase — goes on.
if ! grep -q '^\[GNUPG:\] DECRYPTION_OKAY' "$GPG_STATUS"; then
  echo "this bundle was not encrypted with the DR passphrase — refusing it" >&2
  rm -f "$GPG_LOG" "$GPG_STATUS" "$OUTDIR/.plain.tar"
  exit 1
fi
rm -f "$GPG_LOG" "$GPG_STATUS"

tar -xf "$OUTDIR/.plain.tar" -C "$OUTDIR"
rm -f "$OUTDIR/.plain.tar"

# ── the Secret's env file, rebuilt from the Secret itself ───────────────────
# new_server.sh loads the env file with `kubectl create secret --from-env-file`,
# which reads one KEY=value per line: a value holding a newline (a PEM key, a
# service-account JSON) would keep only its first line, and every other line
# would become a key of its own. A bundle that carries the Secret as JSON
# (dr_backup.sh writes both) gets its env file rebuilt from it with the
# one-line values only; the JSON, every value exactly, is applied afterwards.
SECRET_JSON="$OUTDIR/k8s/sorinflow-secrets.json"
MULTILINE=""
if [ -f "$SECRET_JSON" ]; then
  jq -r '.data | to_entries[] | select(.value | @base64d | test("\n") | not)
         | "\(.key)=\(.value | @base64d)"' "$SECRET_JSON" > "$OUTDIR/k8s/sorinflow-secrets.env"
  MULTILINE="$(jq -r '[.data | to_entries[] | select(.value | @base64d | test("\n")) | .key]
                      | join(", ")' "$SECRET_JSON")"
fi

# ── 4. sanity — what new_server.sh itself insists on ────────────────────────
for need in db/divar_scraper.dump db/globals.sql k8s/sorinflow-secrets.env data-pvc.tar; do
  [ -e "$OUTDIR/$need" ] || { echo "restored bundle is missing $need" >&2; exit 1; }
done

echo
echo "restored into: $OUTDIR"
[ -f "$OUTDIR/db/row-counts.txt" ] && { echo "row counts at the time of the backup:"; sed 's/^/   /' "$OUTDIR/db/row-counts.txt"; }
echo
echo "next, on the new box:"
echo "  bash scripts/new_server.sh $OUTDIR [github-runner-registration-token]"
if [ -n "$MULTILINE" ]; then
  echo
  echo "these Secret keys hold more than one line, so the env file leaves them out: $MULTILINE"
  echo "once new_server.sh has created the Secret, put every key back exactly:"
  echo "  kubectl apply -f $SECRET_JSON"
fi
