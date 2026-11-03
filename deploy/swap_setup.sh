#!/usr/bin/env bash
# swap_setup.sh
# Run on the Oracle VM (1 GB RAM) to add a 2 GB swap file.
# This prevents OOM kills when running Celery worker + outbox relay +
# event consumer simultaneously.
set -euo pipefail

SWAPFILE="/swapfile"
SWAPSIZE="2G"

if [ -f "$SWAPFILE" ]; then
  echo "Swapfile $SWAPFILE already exists. Skipping."
  exit 0
fi

echo "Creating $SWAPSIZE swapfile at $SWAPFILE..."
fallocate -l "$SWAPSIZE" "$SWAPFILE"
chmod 600 "$SWAPFILE"
mkswap "$SWAPFILE"
swapon "$SWAPFILE"

echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab

echo "Swap configured. Verify with: free -m"
echo "Swapiness (optional, reduce for VM): sysctl vm.swappiness=10"
