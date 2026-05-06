#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${DSA_VENV_DIR:-$ROOT_DIR/.venv}"

usage() {
  cat <<'EOF'
Usage: scripts/dev_bootstrap.sh [--backend-only|--with-web|--all]

Prepare local development dependencies for Daily Stock Analysis.

Scopes:
  --backend-only  Backend dependencies: requirements.txt plus flake8 and pytest
  --with-web      Backend dependencies plus Web dependencies: apps/dsa-web/npm ci
  --all           Backend, Web, and Desktop dependencies

Environment:
  DSA_VENV_DIR    Override virtualenv directory. Default: .venv
EOF
}

ensure_venv() {
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "==> dev-bootstrap: creating Python virtualenv at $VENV_DIR"
    python -m venv "$VENV_DIR"
  fi
}

install_backend() {
  ensure_venv
  echo "==> dev-bootstrap: installing backend dependencies"
  "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
  "$VENV_DIR/bin/python" -m pip install flake8 pytest
}

install_npm_package() {
  local app_dir="$1"
  local label="$2"

  if ! command -v npm >/dev/null 2>&1; then
    echo "ERROR: npm is required to install $label dependencies." >&2
    exit 127
  fi

  echo "==> dev-bootstrap: installing $label dependencies"
  (cd "$app_dir" && npm ci)
}

scope="${1:---backend-only}"

case "$scope" in
  --help|-h)
    usage
    ;;
  --backend-only)
    install_backend
    ;;
  --with-web)
    install_backend
    install_npm_package "$ROOT_DIR/apps/dsa-web" "Web"
    ;;
  --all)
    install_backend
    install_npm_package "$ROOT_DIR/apps/dsa-web" "Web"
    install_npm_package "$ROOT_DIR/apps/dsa-desktop" "Desktop"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
