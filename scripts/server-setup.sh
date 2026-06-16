#!/bin/bash
# SorinFlow — Server Setup Script
# Run on: Ubuntu 22.04 | Server: 94.184.6.67
# Usage: bash scripts/server-setup.sh
set -e

SERVER_IP="94.184.6.67"
DOMAIN="sorinflow.com"
GITHUB_REPO="https://github.com/Tecso-Dev/SorinFlow-DaTA-mAmager.git"

echo "======================================"
echo "  SorinFlow Server Setup"
echo "  Server: $SERVER_IP | Domain: $DOMAIN"
echo "======================================"

# ── 1. System update ─────────────────────────────────────────────────────────
echo "[1/7] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq curl git ufw

# ── 2. Firewall ───────────────────────────────────────────────────────────────
echo "[2/7] Configuring firewall..."
ufw --force enable
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 6443/tcp   # k8s API (for GitHub Actions kubectl)

# ── 3. Install k3s ───────────────────────────────────────────────────────────
echo "[3/7] Installing k3s..."
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--tls-san $SERVER_IP --tls-san $DOMAIN" sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' >> /root/.bashrc

echo "Waiting for k3s node to be ready..."
kubectl wait --for=condition=Ready node --all --timeout=120s

# ── 4. Install cert-manager ───────────────────────────────────────────────────
echo "[4/7] Installing cert-manager..."
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.4/cert-manager.yaml
echo "Waiting for cert-manager pods..."
kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=180s

# ── 5. Clone project and apply secrets ────────────────────────────────────────
echo "[5/7] Cloning project..."
if [ ! -d /opt/sorinflow ]; then
    git clone "$GITHUB_REPO" /opt/sorinflow
fi
cd /opt/sorinflow

echo ""
echo ">>> Applying Kubernetes namespace and secrets..."
kubectl apply -f k8s/00-namespace.yaml

echo ""
echo ">>> Creating k8s/01-secrets.yaml with production values..."
cat > /tmp/sorinflow-secrets.yaml << 'SECRETS_EOF'
apiVersion: v1
kind: Secret
metadata:
  name: sorinflow-secrets
  namespace: sorinflow
type: Opaque
stringData:
  POSTGRES_USER: "sorinflow"
  POSTGRES_PASSWORD: "sorinflow_secret_2024"
  POSTGRES_DB: "divar_scraper"
  REDIS_PASSWORD: "redis_secret_2024"
  SECRET_KEY: "sorinflow-production-secret-key-CHANGE-ME-TO-RANDOM-STRING"
  API_KEY: "sorinflow-secret-2024"
  DATABASE_URL: "postgresql+asyncpg://sorinflow:sorinflow_secret_2024@postgres:5432/divar_scraper"
  REDIS_URL: "redis://:redis_secret_2024@redis:6379/0"
SECRETS_EOF
kubectl apply -f /tmp/sorinflow-secrets.yaml
rm /tmp/sorinflow-secrets.yaml

# ── 6. Create ghcr.io pull secret ─────────────────────────────────────────────
echo "[6/7] Setting up GitHub Container Registry pull secret..."
echo ""
echo "Enter your GitHub username (e.g. sahandmoosanezhad):"
read GITHUB_USER
echo "Enter a GitHub Personal Access Token with 'read:packages' scope:"
echo "(Create at: https://github.com/settings/tokens/new → select 'read:packages')"
read -s GITHUB_TOKEN
echo ""

kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username="$GITHUB_USER" \
  --docker-password="$GITHUB_TOKEN" \
  -n sorinflow \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Pull secret created."

# ── 7. Apply remaining manifests ──────────────────────────────────────────────
echo "[7/7] Applying Kubernetes manifests..."
kubectl apply -f k8s/02b-postgres-init-configmap.yaml
kubectl apply -f k8s/02-postgres.yaml
kubectl apply -f k8s/03-redis.yaml
kubectl apply -f k8s/06-cert-issuer.yaml
kubectl apply -f k8s/05-ingress.yaml
# Note: 04-backend.yaml is deployed by GitHub Actions after image is built

echo ""
echo "======================================"
echo "  Setup complete!"
echo "======================================"
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Add KUBE_CONFIG to GitHub Secrets:"
echo "   Go to: https://github.com/Tecso-Dev/SorinFlow-DaTA-mAmager/settings/secrets/actions"
echo "   Add secret name: KUBE_CONFIG"
echo "   Value (copy the output below):"
echo ""
cat /etc/rancher/k3s/k3s.yaml | sed "s/127.0.0.1/$SERVER_IP/g" | base64 -w 0
echo ""
echo ""
echo "2. Make the ghcr.io package public (or the pull secret handles it):"
echo "   https://github.com/orgs/Tecso-Dev/packages"
echo ""
echo "3. Point DNS:"
echo "   sorinflow.com     A  $SERVER_IP"
echo "   www.sorinflow.com A  $SERVER_IP"
echo ""
echo "4. Push to main branch — GitHub Actions will build & deploy automatically."
echo ""
echo "kubectl get pods -n sorinflow   # check status"
