"""
Remote deploy helper for sorinflow.com via paramiko (password or key auth).

Usage:
    SORIN_PW=<password> python scripts/deploy_remote.py

On first run it installs the local SSH public key into the server's
authorized_keys so subsequent runs can use key-based auth (no password).
Then it runs the standard local-registry deploy and streams output.
"""
import os
import sys
from pathlib import Path

import paramiko

HOST = "sorinflow.com"
USER = "admin"
PORT = 22

PUBKEY_PATH = Path.home() / ".ssh" / "id_rsa.pub"

DEPLOY_CMD = (
    "set -e; "
    "cd /tmp/sorin-new 2>/dev/null || git clone "
    "https://github.com/Tecso-Dev/SorinFlow-DaTA-mAmager.git /tmp/sorin-new; "
    "cd /tmp/sorin-new && git fetch origin && git reset --hard origin/main && "
    "docker build -t localhost:5000/sorinflow-backend:latest . && "
    "docker push localhost:5000/sorinflow-backend:latest && "
    "kubectl set image deployment/sorinflow-backend "
    "sorinflow-backend=localhost:5000/sorinflow-backend:latest -n sorinflow && "
    "kubectl rollout status deployment/sorinflow-backend -n sorinflow --timeout=300s"
)


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pw = os.environ.get("SORIN_PW")
    key_file = Path.home() / ".ssh" / "id_rsa"
    # Try key-based auth first (silent if it works), fall back to password
    try:
        client.connect(HOST, port=PORT, username=USER,
                       key_filename=str(key_file), timeout=20,
                       allow_agent=False, look_for_keys=False)
        print("[deploy] connected via SSH key")
        return client
    except Exception as e:
        print(f"[deploy] key auth failed ({e}); trying password")
    if not pw:
        print("ERROR: key auth failed and SORIN_PW not set", file=sys.stderr)
        sys.exit(2)
    # Try plain password auth
    try:
        client.connect(HOST, port=PORT, username=USER, password=pw, timeout=20,
                       allow_agent=False, look_for_keys=False)
        print("[deploy] connected via password")
        return client
    except paramiko.AuthenticationException:
        print("[deploy] password auth failed; trying keyboard-interactive")

    # Fall back to keyboard-interactive (many servers use this instead of 'password')
    transport = paramiko.Transport((HOST, PORT))
    transport.start_client(timeout=20)

    def handler(title, instructions, prompt_list):
        return [pw for _ in prompt_list]

    try:
        transport.auth_interactive(USER, handler)
    except paramiko.SSHException:
        # Some paramiko versions need auth_interactive_dumb
        transport.auth_interactive_dumb(USER, handler)
    if not transport.is_authenticated():
        print("ERROR: all auth methods failed", file=sys.stderr)
        sys.exit(3)
    client._transport = transport
    print("[deploy] connected via keyboard-interactive")
    return client


def install_pubkey(client: paramiko.SSHClient) -> None:
    if not PUBKEY_PATH.exists():
        print("[deploy] no local pubkey to install; skipping")
        return
    pub = PUBKEY_PATH.read_text().strip()
    # Add the key only if it isn't already present
    cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
        f"grep -qF '{pub}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys && echo PUBKEY_OK"
    )
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if "PUBKEY_OK" in out:
        print("[deploy] public key installed (future deploys are passwordless)")
    else:
        print(f"[deploy] pubkey install issue: out={out} err={err}")


def run_deploy(client: paramiko.SSHClient) -> int:
    print("[deploy] starting deploy — streaming output...\n" + "-" * 60)
    chan = client.get_transport().open_session()
    chan.get_pty()
    chan.exec_command(f"bash -lc \"{DEPLOY_CMD}\"")
    while True:
        if chan.recv_ready():
            data = chan.recv(4096).decode(errors="replace")
            sys.stdout.write(data)
            sys.stdout.flush()
        if chan.exit_status_ready() and not chan.recv_ready():
            break
    # Drain remaining
    while chan.recv_ready():
        sys.stdout.write(chan.recv(4096).decode(errors="replace"))
    code = chan.recv_exit_status()
    print("\n" + "-" * 60 + f"\n[deploy] exit code = {code}")
    return code


def main() -> int:
    client = connect()
    try:
        install_pubkey(client)
        return run_deploy(client)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
