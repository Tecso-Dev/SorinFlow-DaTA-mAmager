"""Shared paramiko SSH helper for password/key auth against the server.

Connects with a password (plain or keyboard-interactive) or an SSH key, and
exposes run()/put() helpers that stream output. Used by survey/setup scripts.
"""
import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("SORIN_HOST", "109.122.254.204")
PORT = int(os.environ.get("SORIN_PORT", "22"))
# Iranian VPS providers usually give root; allow override.
USER = os.environ.get("SORIN_USER", "root")
PW = os.environ.get("SORIN_PW")

DEPLOY_KEY = Path(__file__).parent / ".deploy_keys" / "deploy_ed25519"


def connect(user: str | None = None) -> paramiko.SSHClient:
    user = user or USER
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 1) Try dedicated deploy key (silent success once installed).
    if DEPLOY_KEY.exists():
        try:
            client.connect(HOST, port=PORT, username=user,
                           key_filename=str(DEPLOY_KEY), timeout=20,
                           allow_agent=False, look_for_keys=False)
            print(f"[ssh] connected as {user}@{HOST} via deploy key")
            return client
        except Exception:
            pass

    if not PW:
        print("ERROR: no key auth and SORIN_PW not set", file=sys.stderr)
        sys.exit(2)

    # 2) Plain password.
    try:
        client.connect(HOST, port=PORT, username=user, password=PW, timeout=20,
                       allow_agent=False, look_for_keys=False)
        print(f"[ssh] connected as {user}@{HOST} via password")
        return client
    except paramiko.AuthenticationException:
        pass
    except Exception as e:
        print(f"[ssh] password connect error: {e}", file=sys.stderr)

    # 3) keyboard-interactive fallback.
    transport = paramiko.Transport((HOST, PORT))
    transport.start_client(timeout=20)

    def handler(title, instructions, prompt_list):
        return [PW for _ in prompt_list]

    try:
        transport.auth_interactive(user, handler)
    except paramiko.SSHException:
        transport.auth_interactive_dumb(user, handler)
    if not transport.is_authenticated():
        print("ERROR: all auth methods failed", file=sys.stderr)
        sys.exit(3)
    client._transport = transport
    print(f"[ssh] connected as {user}@{HOST} via keyboard-interactive")
    return client


def run(client: paramiko.SSHClient, cmd: str, pty: bool = False) -> int:
    """Run a command, stream stdout+stderr live, return exit code."""
    chan = client.get_transport().open_session()
    if pty:
        chan.get_pty()
    chan.exec_command(cmd)
    chan.settimeout(1.0)
    import socket
    while True:
        try:
            data = chan.recv(4096)
            if not data:
                if chan.exit_status_ready():
                    break
                continue
            sys.stdout.write(data.decode(errors="replace"))
            sys.stdout.flush()
        except socket.timeout:
            if chan.exit_status_ready():
                break
    return chan.recv_exit_status()


def capture(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=60)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    return stdout.channel.recv_exit_status(), out, err


def put(client: paramiko.SSHClient, local: str, remote: str) -> None:
    sftp = client.open_sftp()
    sftp.put(local, remote)
    sftp.close()
    print(f"[ssh] uploaded {local} -> {remote}")
