"""Full server provisioning for SorinFlow on a fresh Ubuntu + k3s box.

Steps:
  1. upload+run bootstrap.sh (swap, firewall, k3s)
  2. generate strong secrets on the server, create the k8s Secret
  3. upload k8s manifests, apply DBs + ingress + traefik ACME
  4. report status

The backend Deployment stays ImagePullBackOff until the first CI build pushes
the image to ghcr.io and (optionally) a ghcr-secret is created. Run with:

    SORIN_HOST=5.160.252.187 SORIN_PW=... python scripts/deploy_server.py
"""
import sys
from pathlib import Path

import _ssh

ROOT = Path(__file__).resolve().parent.parent
K8S = ROOT / "k8s"
REMOTE_DIR = "/opt/sorinflow"

# Manifests to upload (secrets handled separately / dynamically)
MANIFESTS = [
    "00-namespace.yaml",
    "02b-postgres-init-configmap.yaml",
    "02-postgres.yaml",
    "03-redis.yaml",
    "04-backend.yaml",
    "05-ingress.yaml",
    "06-traefik-acme.yaml",
]


def sh(client, cmd):
    code = _ssh.run(client, f"bash -lc {shq(cmd)}")
    if code != 0:
        print(f"\n[deploy] step failed (exit {code})", file=sys.stderr)
        sys.exit(code)


def shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def main() -> int:
    client = _ssh.connect("root")
    try:
        # 1. bootstrap
        print("\n===== UPLOAD + BOOTSTRAP =====")
        client.exec_command(f"mkdir -p {REMOTE_DIR}/k8s")[1].channel.recv_exit_status()
        _ssh.put(client, str(ROOT / "scripts" / "bootstrap.sh"), f"{REMOTE_DIR}/bootstrap.sh")
        sh(client, f"chmod +x {REMOTE_DIR}/bootstrap.sh && {REMOTE_DIR}/bootstrap.sh")

        # 2. secrets — generate strong values on the server, create k8s Secret
        print("\n===== SECRETS =====")
        secret_script = r"""
set -e
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
SECRETS_FILE=/opt/sorinflow/.secrets.env
if [ ! -f "$SECRETS_FILE" ]; then
  PG_PW=$(openssl rand -hex 24)
  RD_PW=$(openssl rand -hex 24)
  SK=$(openssl rand -hex 32)
  AK=$(openssl rand -hex 24)
  cat > "$SECRETS_FILE" <<EOF
POSTGRES_PASSWORD=$PG_PW
REDIS_PASSWORD=$RD_PW
SECRET_KEY=$SK
API_KEY=$AK
EOF
  chmod 600 "$SECRETS_FILE"
  echo "generated new secrets"
else
  echo "reusing existing secrets"
fi
. "$SECRETS_FILE"
kubectl create namespace sorinflow --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic sorinflow-secrets -n sorinflow \
  --from-literal=POSTGRES_USER=sorinflow \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=POSTGRES_DB=divar_scraper \
  --from-literal=REDIS_PASSWORD="$REDIS_PASSWORD" \
  --from-literal=SECRET_KEY="$SECRET_KEY" \
  --from-literal=API_KEY="$API_KEY" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://sorinflow:${POSTGRES_PASSWORD}@postgres:5432/divar_scraper" \
  --from-literal=REDIS_URL="redis://:${REDIS_PASSWORD}@redis:6379/0" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "secret applied"
"""
        sh(client, secret_script)

        # 3. upload + apply manifests
        print("\n===== UPLOAD MANIFESTS =====")
        for m in MANIFESTS:
            _ssh.put(client, str(K8S / m), f"{REMOTE_DIR}/k8s/{m}")

        print("\n===== APPLY MANIFESTS =====")
        apply = "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; cd /opt/sorinflow; " + " ".join(
            f"kubectl apply -f k8s/{m};" for m in MANIFESTS
        )
        sh(client, apply)

        # 4. status
        print("\n===== STATUS =====")
        _ssh.run(client, "bash -lc 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; "
                         "kubectl get pods -n sorinflow -o wide; echo; "
                         "kubectl get ingress -n sorinflow'")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
