# StoreFlow VM Deployment

Architecture:

- FastAPI Cloud: web app only (HTTP)
- Oracle VM (1 GB, IP `130.162.165.234`, user `ubuntu`): Celery worker, beat, outbox relay, event consumer
- CloudAMQP: RabbitMQ broker (existing)
- Cloud Redis (Layerbase): Celery result backend + cache (existing)
- Neon: Postgres (existing)

## Repo on the VM

```
/opt/storeflow   (venv: /opt/storeflow/.venv, env: /opt/storeflow/.env)
```

SSH (from Windows):

```
ssh -i "C:/Users/kenne/Downloads/ssh-key-2026-11-03.key" ubuntu@130.162.165.234
```

## Deploy / update steps

```
cd /opt/storeflow
git pull
bash uv sync --frozen          # use: uv sync --frozen (uv in ~/.local/bin)
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now celery-worker outbox-relay event-consumer celery-beat
```

One-shot alternative after a fresh clone:

```
sudo bash deploy/deploy_vm.sh
```

## .env notes

- `CELERY_RESULT_BACKEND` must end with `?ssl_cert_reqs=CERT_NONE`
  (redis-py refuses a bare `rediss://` URL — see the MITM warning in beat logs).
- `RABBIT_MQ_URL` doubles as the Celery broker via alias `celery_broker_url`.

## Watching logs

On the VM:

```
bash deploy/watch.sh                  # status of all services + last lines
bash deploy/watch.sh event-consumer   # follow one service
```

From Windows:

```
ssh -i "C:/Users/kenne/Downloads/ssh-key-2026-11-03.key" ubuntu@130.162.165.234 "cd /opt/storeflow && bash deploy/watch.sh"
ssh -i "C:/Users/kenne/Downloads/ssh-key-2026-11-03.key" ubuntu@130.162.165.234 "sudo journalctl -u event-consumer -f"
```

Monitor memory with `free -m` (952 MB RAM + 2 GB swap; expect ~500 MB used).
