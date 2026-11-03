# StoreFlow VM Deployment
# ======================
# Architecture:
#   - FastAPI Cloud: web app only (HTTP)
#   - Oracle VM (1 GB): Celery worker, outbox relay, event consumer, (+beat)
#   - CloudAMQP: RabbitMQ broker (existing)
#   - Cloud Redis: Celery backend + cache (existing)
#   - Neon: Postgres (existing)

# Prerequisites on Oracle VM:
#   - Ubuntu 22.04 LTS
#   - 1 GB RAM + 2 GB swap (see deploy/swap_setup.sh)
#   - Outbound HTTPS to CloudAMQP, Cloud Redis, Neon

# Deployment steps (run on the VM):
#   1. git clone <salesos-repo> /opt/salesos
#   2. sudo bash /opt/salesos/deploy/deploy_vm.sh
#   3. Edit /opt/salesos/.env with real credentials
#   4. python3 -m venv /opt/salesos/.venv
#   5. /opt/salesos/.venv/bin/pip install -r /opt/salesos/requirements.txt
#   6. sudo systemctl enable --now celery-worker
#   7. sudo systemctl enable --now outbox-relay
#   8. sudo systemctl enable --now event-consumer
#   9. (optional) sudo systemctl enable --now celery-beat

# Verify:
#   sudo journalctl -u celery-worker -f
#   sudo journalctl -u outbox-relay -f
#   sudo journalctl -u event-consumer -f
#   free -m   # check swap is active
