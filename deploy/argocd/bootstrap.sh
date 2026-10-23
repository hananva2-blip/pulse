#!/usr/bin/env bash
# Installs Argo CD into the local k3d cluster and hands the cluster over to Git.
set -euo pipefail
cd "$(dirname "$0")/../.."

CONTEXT="${CONTEXT:-k3d-pulse}"
CHART_VERSION=10.10.3   # Argo CD v3.5.5

helm repo add argo https://argoproj.github.io/argo-helm >/dev/null
helm repo update argo >/dev/null
helm --kube-context "$CONTEXT" upgrade --install argocd argo/argo-cd \
  --version "$CHART_VERSION" -n argocd --create-namespace \
  -f deploy/argocd/values.yaml --wait=legacy --timeout 10m
# --wait=legacy: Helm 4's default "watcher" strategy hangs on this chart's pre-install hook

# From here on, everything else comes from Git
kubectl --context "$CONTEXT" apply -f deploy/argocd/root.yaml

echo "Argo CD: http://argocd.localhost:8880  user: admin  password:"
kubectl --context "$CONTEXT" -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
echo
