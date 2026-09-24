"""
Disaster recovery — the round trip, for real.

scripts/dr_backup.sh runs on the HOST as root and talks to the cluster only
through `kubectl`. This test stands the host up as a temp directory tree plus
a fake `kubectl` (a small script, used only here) that maps the handful of
calls dr_backup.sh makes onto local equivalents:

  * `get secret ... -o json|jsonpath=...`   -> a fixture secret JSON
  * `exec postgres-0 -- pg_dump/pg_dumpall/psql`  -> `docker exec` into a
    throwaway Postgres container (p1e-pg)
  * `exec deploy/backend -- python -m app.services.dr_backup ship|alert`
    -> the real module, for real, with TELEGRAM_API_BASE pointed at a local
    fake HTTP server in this process instead of api.telegram.org

Then scripts/dr_restore.sh runs on what that fake Telegram "received" —
proving the whole path: build -> encrypt -> split -> ship -> download ->
verify -> decrypt -> restore, with nothing here reaching a real cluster or
Telegram.
"""
import email
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
from base64 import b64encode
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PY = os.environ.get("DR_TEST_PY") or sys.executable
PG_CONTAINER = "p1e-pg"

FAKE_KUBECTL = r"""#!/usr/bin/env bash
# Fake kubectl for the DR round-trip test. Understands exactly the calls
# scripts/dr_backup.sh makes and nothing else.
set -euo pipefail

if [ "${1:-}" = "-n" ]; then shift; shift; fi

VERB="${1:-}"; shift || true

case "$VERB" in
  get)
    shift               # "secret"
    shift               # secret name
    OFMT=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -o) OFMT="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    case "$OFMT" in
      json) cat "$FAKE_SECRET_JSON" ;;
      # real kubectl returns the field AS STORED — still base64, like the
      # Secret's .data always is; dr_backup.sh does the `base64 -d` itself.
      jsonpath=*) jq -r '.data.DR_BACKUP_PASSPHRASE // ""' "$FAKE_SECRET_JSON" ;;
      *) echo "fake kubectl: unhandled 'get -o $OFMT'" >&2; exit 1 ;;
    esac
    ;;
  exec)
    POD="$1"; shift
    [ "${1:-}" = "--" ] && shift
    CMD="$1"; shift
    case "$POD" in
      postgres-0)
        case "$CMD" in
          pg_dump|pg_dumpall|psql) exec docker exec "$PG_CONTAINER" "$CMD" "$@" ;;
          *) echo "fake kubectl: unhandled postgres-0 command: $CMD" >&2; exit 1 ;;
        esac
        ;;
      deploy/backend)
        [ "$CMD" = "python" ] || { echo "fake kubectl: unhandled backend command: $CMD" >&2; exit 1; }
        ARGS=()
        for a in "$@"; do
          case "$a" in
            /app/data/*) ARGS+=("${DR_DATA_DIR}${a#/app/data}") ;;
            *) ARGS+=("$a") ;;
          esac
        done
        TOKEN="$(jq -r '.data.TELEGRAM_BOT_TOKEN // "" | @base64d' "$FAKE_SECRET_JSON")"
        CHAT="$(jq -r '.data.TELEGRAM_CHAT_ID // "" | @base64d' "$FAKE_SECRET_JSON")"
        SKEY="$(jq -r '.data.SECRET_KEY // "" | @base64d' "$FAKE_SECRET_JSON")"
        cd "$FAKE_REPO_DIR"
        exec env \
          SORINFLOW_DATA_DIR="$DR_DATA_DIR" \
          DATABASE_URL="$FAKE_DATABASE_URL" \
          SECRET_KEY="$SKEY" \
          TELEGRAM_BOT_TOKEN="$TOKEN" \
          TELEGRAM_CHAT_ID="$CHAT" \
          TELEGRAM_API_BASE="$FAKE_TG_API_BASE" \
          TELEGRAM_DIRECT_FIRST=0 \
          LOGS_PATH=/tmp IMAGES_PATH=/tmp \
          "$FAKE_PY" "${ARGS[@]}"
        ;;
      *) echo "fake kubectl: unhandled pod: $POD" >&2; exit 1 ;;
    esac
    ;;
  *) echo "fake kubectl: unhandled verb: $VERB" >&2; exit 1 ;;
esac
"""


class _TelegramHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        with self.server.lock:
            if "multipart/form-data" in ctype:
                msg = email.message_from_bytes(
                    b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body,
                    policy=policy.default,
                )
                for part in msg.iter_parts():
                    filename = part.get_filename()
                    if not filename:
                        continue
                    payload = part.get_payload(decode=True) or b""
                    (self.server.storage_dir / filename).write_bytes(payload)
                    self.server.records.append({
                        "path": self.path, "name": filename, "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    })
            else:
                self.server.records.append({"path": self.path, "body": body.decode("utf-8", "replace")})
        resp = json.dumps({"ok": True, "result": {}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


def _docker_exec(*args, **kw):
    kw.setdefault("check", True)
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(["docker", "exec", PG_CONTAINER, *args], **kw)


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    assert shutil.which("docker"), "docker is required for the DR round-trip test"
    assert shutil.which("gpg"), "gpg is required for the DR round-trip test"

    base = tmp_path_factory.mktemp("dr")
    pvc, traefik, fakebin, work, storage = (base / n for n in
        ("pvc", "traefik", "fakebin", "work", "telegram-storage"))
    for d in (pvc, traefik, fakebin, work, storage):
        d.mkdir(parents=True)

    # ── fixture data-pvc: what must survive vs. what must not ──────────────
    (pvc / "images" / "avatars").mkdir(parents=True)
    (pvc / "images" / "avatars" / "tok1.jpg").write_bytes(b"AVATAR-BYTES")
    (pvc / "images" / "12345").mkdir(parents=True)
    (pvc / "images" / "12345" / "photo1.jpg").write_bytes(b"LISTING-PHOTO-BYTES")
    (pvc / "cookies").mkdir()
    (pvc / "cookies" / "09121234567.json").write_text('{"cookies": [{"name": "sid", "value": "x"}]}')
    prof = pvc / "profiles" / "09121234567"
    (prof / "Default" / "Cache").mkdir(parents=True)
    (prof / "Default" / "Cache" / "junk").write_bytes(b"CACHE-JUNK")
    (prof / "Default" / "Preferences").write_text('{"pref": true}')
    (prof / "GrShaderCache").mkdir(parents=True)
    (prof / "GrShaderCache" / "shader0").write_bytes(b"SHADER-JUNK")
    (traefik / "acme.json").write_text('{"letsencrypt": {"Account": {}}}')

    # ── throwaway postgres (never sorinflow-local-*) ────────────────────────
    subprocess.run(["docker", "rm", "-f", PG_CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", PG_CONTAINER,
                     "-e", "POSTGRES_PASSWORD=x", "-p", "5498:5432", "postgres:16-alpine"],
                    check=True, capture_output=True)
    for _ in range(60):
        r = subprocess.run(["docker", "exec", PG_CONTAINER, "pg_isready", "-U", "postgres"],
                            capture_output=True)
        if r.returncode == 0:
            break
        time.sleep(1)
    else:
        subprocess.run(["docker", "rm", "-f", PG_CONTAINER], capture_output=True)
        raise RuntimeError("throwaway postgres never became ready")

    _docker_exec("psql", "-U", "postgres", "-c", "CREATE DATABASE divar_scraper")
    _docker_exec("psql", "-U", "postgres", "-d", "divar_scraper", "-c",
                 "CREATE TABLE users (id serial primary key, name text); "
                 "INSERT INTO users (name) VALUES ('a'), ('b'), ('c');")
    _docker_exec("psql", "-U", "postgres", "-d", "divar_scraper", "-c",
                 "CREATE TABLE properties (id serial primary key, title text); "
                 "INSERT INTO properties (title) VALUES ('p1'), ('p2');")
    expected_counts = {"users": 3, "properties": 2}

    # ── fixture Secret ───────────────────────────────────────────────────────
    passphrase = "correct horse battery staple — test only"
    plain_secret = {
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "x",
        "POSTGRES_DB": "divar_scraper",
        "DATABASE_URL": "postgresql+asyncpg://postgres:x@postgres:5432/divar_scraper",
        "SECRET_KEY": "test-secret-key-0123456789abcdef",
        "API_KEY": "test-api-key",
        "DR_BACKUP_PASSPHRASE": passphrase,
        "TELEGRAM_BOT_TOKEN": "123456:FAKE-BOT-TOKEN-FOR-TESTS",
        "TELEGRAM_CHAT_ID": "999999",
    }
    secret_json = base / "secret.json"
    secret_json.write_text(json.dumps({
        "data": {k: b64encode(v.encode()).decode() for k, v in plain_secret.items()}
    }))

    # ── fake kubectl on its own bin dir ─────────────────────────────────────
    kubectl_path = fakebin / "kubectl"
    kubectl_path.write_text(FAKE_KUBECTL)
    kubectl_path.chmod(kubectl_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # ── fake Telegram ────────────────────────────────────────────────────────
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _TelegramHandler)
    httpd.lock = threading.Lock()
    httpd.records = []
    httpd.storage_dir = storage
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    tg_base = f"http://127.0.0.1:{httpd.server_address[1]}"

    env = os.environ.copy()
    env["PATH"] = f"{fakebin}{os.pathsep}{env['PATH']}"
    env["DR_DATA_DIR"] = str(pvc)
    env["DR_TRAEFIK_DIR"] = str(traefik)
    env["DR_WORK_DIR"] = str(work)
    env["KUBECONFIG"] = "/dev/null"
    env["FAKE_SECRET_JSON"] = str(secret_json)
    env["PG_CONTAINER"] = PG_CONTAINER
    env["FAKE_REPO_DIR"] = str(REPO)
    env["FAKE_PY"] = PY
    env["FAKE_DATABASE_URL"] = "postgresql+asyncpg://postgres:x@127.0.0.1:5498/divar_scraper"
    env["FAKE_TG_API_BASE"] = tg_base

    try:
        result = subprocess.run(["bash", str(REPO / "scripts" / "dr_backup.sh")],
                                 env=env, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                f"scripts/dr_backup.sh failed (exit {result.returncode}) — nothing downstream "
                f"can run:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        manifests = list(storage.glob("*.manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError(f"expected exactly one manifest to reach the fake Telegram server, got {manifests}")
        manifest = json.loads(manifests[0].read_text())
        stamp = manifest["stamp"]

        # The restore step is run ONCE here, on what the fake Telegram server
        # actually received, so every test below just reads its output —
        # nobody depends on another test having run first.
        restore_result = _run_restore(passphrase, storage)
        if restore_result.returncode != 0:
            raise RuntimeError(
                f"scripts/dr_restore.sh failed (exit {restore_result.returncode}):\n"
                f"stdout:\n{restore_result.stdout}\nstderr:\n{restore_result.stderr}"
            )

        yield {
            "backup_result": result, "restore_result": restore_result,
            "base": base, "pvc": pvc, "storage": storage, "manifest": manifest,
            "outdir": storage / f"restored-{stamp}",
            "plain_secret": plain_secret, "passphrase": passphrase,
            "records": httpd.records, "expected_counts": expected_counts, "env": env,
        }
    finally:
        httpd.shutdown()
        httpd.server_close()
        subprocess.run(["docker", "rm", "-f", PG_CONTAINER], capture_output=True)


def _copy_parts_only(pipeline, dest):
    """A fresh copy of what Telegram holds — manifest + parts — WITHOUT the
    restored-<stamp> directory the fixture's own restore run already left in
    pipeline['storage'], so a second, tampered restore attempt here starts
    from a clean slate and its "nothing was restored" assertion means it."""
    dest.mkdir(parents=True)
    for f in pipeline["storage"].iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    return dest


def _run_restore(passphrase, storage_dir, extra_env=None):
    env = os.environ.copy()
    env["DR_BACKUP_PASSPHRASE"] = passphrase
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(REPO / "scripts" / "dr_restore.sh"), str(storage_dir)],
                           env=env, capture_output=True, text=True, timeout=120)


class TestBackupShipsSuccessfully:

    def test_dr_backup_exits_zero(self, pipeline):
        r = pipeline["backup_result"]
        assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"

    def test_manifest_and_parts_reached_telegram(self, pipeline):
        manifest = pipeline["manifest"]
        assert manifest["parts"], "manifest lists no parts"
        for p in manifest["parts"]:
            got = pipeline["storage"] / p["name"]
            assert got.exists(), f"part {p['name']} never reached the fake Telegram server"
            assert got.stat().st_size == p["size"]
            assert hashlib.sha256(got.read_bytes()).hexdigest() == p["sha256"]

    def test_parts_stay_under_telegrams_50mb_limit(self, pipeline):
        for p in pipeline["manifest"]["parts"]:
            assert p["size"] <= 50 * 1024 * 1024

    def test_header_message_mentions_the_bundle(self, pipeline):
        created_at = pipeline["manifest"]["created_at"]
        texts = [r["body"] for r in pipeline["records"] if "body" in r]
        assert any(created_at in t for t in texts), texts
        # the row counts travel in the same message
        assert any("users 3" in t or "properties 2" in t for t in texts), texts


class TestRestore:
    """These all read what the fixture's single dr_restore.sh run produced —
    nobody here re-runs it, so test order does not matter."""

    def test_restore_prints_the_new_server_command(self, pipeline):
        r = pipeline["restore_result"]
        assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        assert "sha256 OK" in r.stdout
        assert "scripts/new_server.sh" in r.stdout

    def test_restored_bundle_has_what_new_server_sh_requires(self, pipeline):
        outdir = pipeline["outdir"]
        for needed in ("db/divar_scraper.dump", "db/globals.sql", "db/row-counts.txt",
                       "k8s/sorinflow-secrets.env", "k8s/traefik-acme.json", "data-pvc.tar"):
            assert (outdir / needed).exists(), needed

    def test_row_counts_file_matches_what_was_seeded(self, pipeline):
        outdir = pipeline["outdir"]
        counts = dict(
            line.split() for line in (outdir / "db" / "row-counts.txt").read_text().splitlines() if line.strip()
        )
        for table, n in pipeline["expected_counts"].items():
            assert counts.get(table) == str(n), counts

    def test_secrets_env_round_trips_exactly(self, pipeline):
        outdir = pipeline["outdir"]
        restored = dict(
            line.split("=", 1) for line in (outdir / "k8s" / "sorinflow-secrets.env").read_text().splitlines()
            if "=" in line
        )
        assert restored == pipeline["plain_secret"]

    def test_traefik_acme_json_round_trips(self, pipeline):
        original = (pipeline["base"] / "traefik" / "acme.json").read_text()
        assert (pipeline["outdir"] / "k8s" / "traefik-acme.json").read_text() == original

    def test_data_pvc_keeps_avatars_cookies_and_profiles_but_drops_photos_and_cache(self, pipeline):
        outdir = pipeline["outdir"]
        extract = outdir / "_extracted"
        extract.mkdir(exist_ok=True)
        with tarfile.open(outdir / "data-pvc.tar") as tf:
            tf.extractall(extract, filter="data")

        assert (extract / "images" / "avatars" / "tok1.jpg").read_bytes() == b"AVATAR-BYTES"
        assert (extract / "cookies" / "09121234567.json").exists()
        assert (extract / "profiles" / "09121234567" / "Default" / "Preferences").exists()

        assert not (extract / "images" / "12345").exists(), "listing photos should have been excluded"
        assert not (extract / "profiles" / "09121234567" / "Default" / "Cache").exists(), \
            "Chromium cache should have been excluded"
        assert not (extract / "profiles" / "09121234567" / "GrShaderCache").exists(), \
            "Chromium shader cache should have been excluded"

    def test_dump_restores_into_a_fresh_database_with_matching_row_counts(self, pipeline):
        outdir = pipeline["outdir"]
        _docker_exec("psql", "-U", "postgres", "-c", "DROP DATABASE IF EXISTS restored_check")
        _docker_exec("psql", "-U", "postgres", "-c", "CREATE DATABASE restored_check")
        with open(outdir / "db" / "globals.sql", "rb") as f:
            subprocess.run(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "postgres", "-d", "restored_check"],
                            stdin=f, capture_output=True)  # roles may already exist — best effort, like new_server.sh
        with open(outdir / "db" / "divar_scraper.dump", "rb") as f:
            r = subprocess.run(["docker", "exec", "-i", PG_CONTAINER, "pg_restore", "-U", "postgres",
                                 "-d", "restored_check", "--no-owner", "--clean", "--if-exists",
                                 "--exit-on-error"], stdin=f, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

        for table, n in pipeline["expected_counts"].items():
            out = _docker_exec("psql", "-U", "postgres", "-d", "restored_check", "-tAc",
                                f'select count(*) from "{table}"')
            assert out.stdout.strip() == str(n), f"{table}: {out.stdout!r}"
        _docker_exec("psql", "-U", "postgres", "-c", "DROP DATABASE restored_check")


class TestTamperIsRefused:

    def test_a_flipped_byte_is_refused_and_nothing_is_restored(self, pipeline, tmp_path):
        manifest = pipeline["manifest"]
        copy_dir = _copy_parts_only(pipeline, tmp_path / "tampered")
        target = copy_dir / manifest["parts"][0]["name"]
        data = bytearray(target.read_bytes())
        data[0] ^= 0xFF
        target.write_bytes(data)

        r = _run_restore(pipeline["passphrase"], copy_dir)
        assert r.returncode != 0
        assert "CHECKSUM MISMATCH" in r.stderr
        assert manifest["parts"][0]["name"] in r.stderr
        assert not (copy_dir / f"restored-{manifest['stamp']}").exists()

    def test_a_missing_part_is_refused_and_named(self, pipeline, tmp_path):
        manifest = pipeline["manifest"]
        copy_dir = _copy_parts_only(pipeline, tmp_path / "missing")
        missing_name = manifest["parts"][-1]["name"]
        (copy_dir / missing_name).unlink()

        r = _run_restore(pipeline["passphrase"], copy_dir)
        assert r.returncode != 0
        assert "MISSING part" in r.stderr
        assert missing_name in r.stderr
        assert not (copy_dir / f"restored-{manifest['stamp']}").exists()
