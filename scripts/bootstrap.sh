#!/usr/bin/env bash
# Bootstrap the SorinFlow server: swap, firewall, k3s (single node).
# Idempotent — safe to re-run. Runs as root on Ubuntu.
set -euo pipefail

SERVER_IP="${SERVER_IP:-5.160.252.187}"
DOMAIN="${DOMAIN:-sorinflow.com}"

echo "==== [1/4] Swap (server has little RAM, no swap) ===="
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "swap enabled (4G)"
else
  echo "swap already present"
fi

echo "==== [2/4] Base packages + firewall ===="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git ufw >/dev/null
ufw allow 22/tcp   >/dev/null
ufw allow 80/tcp   >/dev/null
ufw allow 443/tcp  >/dev/null
ufw allow 6443/tcp >/dev/null   # k3s API (optional external kubectl)
ufw --force enable >/dev/null
echo "firewall: 22/80/443/6443 allowed"

echo "==== [3/4] Install k3s (Traefik + local-path built in) ===="
if ! systemctl is-active --quiet k3s; then
  curl -sfL https://get.k3s.io | \
    INSTALL_K3S_EXEC="--tls-san ${SERVER_IP} --tls-san ${DOMAIN} --write-kubeconfig-mode 644" \
    sh -
else
  echo "k3s already active"
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
grep -q 'KUBECONFIG=/etc/rancher/k3s' /root/.bashrc || \
  echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' >> /root/.bashrc

echo "==== [4/4] Wait for node Ready ===="
kubectl wait --for=condition=Ready node --all --timeout=180s
kubectl get nodes -o wide
echo "BOOTSTRAP_OK"
