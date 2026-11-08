# Docker setup

Runs the Mimikiku server and Mongo with NVIDIA GPU support.

## Requirements

- Docker Engine 20.10+ and Docker Compose 2.24+
- NVIDIA Container Toolkit and an NVIDIA GPU

Check GPU access:

```bash
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

## Start

1. Copy `.env.example` to `.env` in the repo root. Set the Hugging Face token (see the [main README](../README.md#hugging-face-token)), `PUBLIC_ORIGIN` and `STORE_AUDIO`. Compose variables are optional, in `docker/.env` (see `docker/.env.example`).
2. Build and start:
   ```bash
   ./docker-start.sh --build
   ```
3. Open http://localhost:7777 and sign in with `MIMIKIKU_ADMIN_EMAIL` and `MIMIKIKU_ADMIN_PASSWORD` from `.env`. The admin is created on the first start, when there are no users yet. Change the password under Account.

Without those variables, or after a lockout, create or reset an admin from the CLI. It prints a one-time set-password link:

```bash
docker compose -f docker/docker-compose.yml -p mimikiku exec server \
  python manage_users.py create-admin you@example.com   # or: reset-link <email>
```

Admins add users in the web app (Users) and change transcription settings under Service.

API docs: http://localhost:7777/docs

## Day to day

```bash
docker compose -f docker/docker-compose.yml -p mimikiku logs -f server    # logs
docker compose -f docker/docker-compose.yml -p mimikiku exec server bash  # shell
docker compose -f docker/docker-compose.yml -p mimikiku down              # stop
```

Rebuild after code changes with `./docker-start.sh --build`. For a clean rebuild:

```bash
docker compose -f docker/docker-compose.yml -p mimikiku build --no-cache
```

## Volumes and backups

- `../logs` → `/app/logs`: application logs, on the host.
- `model-cache`: Hugging Face model cache. The first start downloads the models.
- `mongo-data`: users, transcripts, settings, optional audio. Back it up with:
  ```bash
  docker exec mimikiku-mongo mongodump --archive > mimikiku.archive
  ```

## Several servers

Run the stack on each GPU machine. Set `MONGO_URI` in `docker/.env` to one shared Mongo, and give each server its own `MIMIKIKU_INSTANCE`. Sign-ins, transcripts and settings work on any server. A recording stays on the server it started on. The admin Service page shows which servers need a restart to pick up model settings.

## Telemetry

Off by default. See [OLLY.md](OLLY.md).

## Troubleshooting

**No GPU detected.** Run the `nvidia-smi` check above. Make sure the NVIDIA Container Toolkit is installed.

**Port 7777 is taken.** Change the port mapping in `docker/docker-compose.yml`.

**`RuntimeError: operator torchvision::nms does not exist`.** pip overwrote the base image's torch packages. Do a clean rebuild (above).

**NumPy errors in the diarizer.** NumPy must be below 2.0. The Dockerfile pins it.

**CUDA multiprocessing errors.** The container runs with `ipc: host` and the spawn start method.
