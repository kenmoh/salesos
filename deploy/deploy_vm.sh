#!/usr/bin/env bash
# deploy_vm.sh
# Run this on the Oracle VM after cloning the repo.
# Uses uv (the project's package manager) instead of pip.
set -euo pipefail

REPO_DIR="/opt/salesos"
VM_USER="salesos"
PYTHON_MINOR="14"

echo "=== 0. Remove any failed pip install attempt ==="
# Uninstall any partially installed project packages from pip
sudo -u "$VM_USER" python3 -m pip uninstall -y salesos 2>/dev/null || true
sudo -u "$VM_USER" python3 -m pip uninstall -y pydantic pydantic-settings celery redis sqlalchemy 2>/dev/null || true

echo "=== 1. Create system user ==="
id "$VM_USER" 2>/dev/null || sudo useradd -m -s /bin/bash "$VM_USER"

echo "=== 2. Copy repo to $REPO_DIR ==="
sudo mkdir -p "$REPO_DIR"
sudo cp -r . "$REPO_DIR/"
sudo chown -R "$VM_USER:$VM_USER" "$REPO_DIR"

echo "=== 3. Setup swap ==="
sudo bash "$REPO_DIR/deploy/swap_setup.sh"

echo "=== 4. Install Python 3.14+ (required by pyproject.toml) ==="
if ! python3 --version 2>/dev/null | grep -qE "3\.1[4-9]"; then
  sudo apt update
  sudo apt install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt install -y python3.14 python3.14-venv python3.14-dev
fi
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.14 1 || true

echo "=== 5. Install uv ==="
sudo -u "$VM_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# Make uv available system-wide via the user's local bin
export PATH="$HOME/.local/bin:$PATH"

echo "=== 6. Create .env ==="
if [ ! -f "$REPO_DIR/.env" ]; then
  sudo cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
  echo "WARNING: Edit $REPO_DIR/.env with real values before starting services."
fi
sudo chown "$VM_USER:$VM_USER" "$REPO_DIR/.env"

echo "=== 7. Install systemd units ==="
sudo cp "$REPO_DIR/deploy/celery-worker.service" /etc/systemd/system/
sudo cp "$REPO_DIR/deploy/outbox-relay.service" /etc/systemd/system/
sudo cp "$REPO_DIR/deploy/event-consumer.service" /etc/systemd/system/
sudo cp "$REPO_DIR/deploy/celery-beat.service" /etc/systemd/system/
sudo systemctl daemon-reload

echo "=== 8. Next steps ==="
echo "  1. Edit $REPO_DIR/.env with your production values (DATABASE_URL, RABBIT_MQ_URL, etc.)"
echo "  2. cd $REPO_DIR && uv sync --frozen"
echo "  3. sudo systemctl enable --now celery-worker"
echo "  4. sudo systemctl enable --now outbox-relay"
echo "  5. sudo systemctl enable --now event-consumer"
echo "  6. (Optional) sudo systemctl enable --now celery-beat"
echo "  7. Check logs: sudo journalctl -u celery-worker -f"
