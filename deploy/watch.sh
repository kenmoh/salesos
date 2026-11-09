#!/usr/bin/env bash
# watch.sh — convenience tails for the StoreFlow worker services on the Oracle VM.
# Usage (on the VM):    bash deploy/watch.sh [service]
# Usage (from Windows): ssh -i "C:/Users/kenne/Downloads/ssh-key-2026-11-03.key" \
#                         ubuntu@130.162.165.234 "cd /opt/storeflow && bash deploy/watch.sh"

VM_IP="130.162.165.234"
VM_KEY="C:/Users/kenne/Downloads/ssh-key-2026-11-03.key"
VM_USER="ubuntu"
REPO_DIR="/opt/storeflow"

SERVICES=(
  "celery-worker"
  "celery-beat"
  "outbox-relay"
  "event-consumer"
)

# If a service name was passed, tail just that one; otherwise show all statuses.
if [ -n "$1" ]; then
  exec sudo journalctl -u "$1" -f --no-pager
fi

echo "=== Service status ==="
systemctl is-active "${SERVICES[@]}" 2>/dev/null | paste -sd' ' -
for s in "${SERVICES[@]}"; do
  printf "%-16s %s\n" "$s" "$(systemctl is-active "$s")"
done
echo
echo "=== Last 5 lines per service ==="
for s in "${SERVICES[@]}"; do
  echo "--- $s ---"
  sudo journalctl -u "$s" -n 5 --no-pager | tail -5
done
echo
echo "Follow a service:  bash deploy/watch.sh <service>"
echo "Memory:            free -m"
echo "From Windows:      ssh -i \"$VM_KEY\" $VM_USER@$VM_IP \"cd $REPO_DIR && bash deploy/watch.sh celery-worker\""
