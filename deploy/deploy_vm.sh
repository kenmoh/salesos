#!/usr/bin/env bash
# deploy_vm.sh
# Run this from the repo root on the Oracle VM (assumes repo is cloned at /opt/storeflow).
# Uses uv (the project's package manager) instead of pip.
set -euo pipefail

REPO_DIR="/opt/storeflow"
VM_USER="ubuntu"

echo "=== 1. Setup swap (2 GB for 1 GB RAM VM) ==="
sudo bash "$REPO_DIR/deploy/swap_setup.sh"

echo "=== 2. Check Python 3.14+ (required by pyproject.toml) ==="
if ! python3 --version 2>/dev/null | grep -qE "3\.1[4-9]"; then
  sudo apt update
  sudo apt install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt install -y python3.14 python3.14-venv python3.14-dev
  sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.14 1 || true
fi

echo "=== 3. Install uv ==="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "=== 4. Sync dependencies ==="
cd "$REPO_DIR"
sudo chown -R "$VM_USER:$VM_USER" "$REPO_DIR"
export UV_PYTHON=python3.14
uv sync --frozen

echo "=== 5. Create .env (if missing) ==="
if [ ! -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
  echo "WARNING: Edit $REPO_DIR/.env with real values before starting services."
fi

echo "=== 6. Install systemd units ==="
sudo cp "$REPO_DIR/deploy/celery-worker.service" /etc/systemd/system/
sudo cp "$REPO_DIR/deploy/outbox-relay.service" /etc/systemd/system/
sudo cp "$REPO_DIR/deploy/event-consumer.service" /etc/systemd/system/
sudo cp "$REPO_DIR/deploy/celery-beat.service" /etc/systemd/system/
sudo systemctl daemon-reload

echo "=== 7. Start services ==="
sudo systemctl enable --now celery-worker
sudo systemctl enable --now outbox-relay
sudo systemctl enable --now event-consumer

echo "=== Done ==="
echo "Verify: sudo journalctl -u celery-worker -f"
echo "Verify: sudo journalctl -u outbox-relay -f"
echo "Verify: sudo journalctl -u event-consumer -f"
echo "Memory: free -m"
