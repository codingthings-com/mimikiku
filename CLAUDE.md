# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

Mimikiku: real-time transcription with speaker labels, over WebSocket, using NVIDIA NeMo models (Nemotron ASR, Sortformer diarizer). Runs on an NVIDIA GPU, **in Docker only**. Multi-user: per-user transcripts saved server-side. Admins manage accounts and settings.

## Commands

```bash
# Tests (host venv; the server itself only runs in Docker)
source .venv/bin/activate
python -m pytest server/tests/                        # whole suite
python -m pytest server/tests/test_x.py -k name       # one test
python -m pytest server/tests/ -x -q                  # stop on first failure

# Docker (from repo root)
./docker-start.sh [--build]     # server on :7777 + mongo
./docker-logs.sh [--tail N]
./docker-stop.sh

# Admin CLI (inside the container)
docker compose -f docker/docker-compose.yml -p mimikiku exec server \
  python manage_users.py create-admin you@example.com   # also: reset-link <email>, list
```

Logs: `listen.py` writes `mimikiku_<timestamp>.log` files to `../logs/` (`/app/logs` in Docker), rotated at 50 MB × 5. `logs/` is gitignored.

## Layout and flow

- `server/listen.py`: FastAPI app, `/listen` WebSocket, `/api/*` monitoring routes.
- `server/transcription_pipeline_manager.py`: per-connection state and the Rx pipeline.
- `server/transcriber.py`: `NemotronASR` (streaming, one session per connection) and `SubprocessDiarizer` (talks to `diarize_worker.py`).
- `server/streaming_audio.py`: incremental log-mel features, shared by ASR and the diarizer.
- `server/model_management/`: model load/unload, TTL, circuit breaker, retries, GPU memory tracking.
- `server/config.py`: `AppConfig`, model configs, streaming tuning. Models load at a pinned Hub revision (`MIMIKIKU_ASR_MODEL`, `MIMIKIKU_DIAR_MODEL` and their `_REVISION` vars).
- `server/accounts/`: users, sessions, API tokens, invite/reset links, transcripts, audio storage, routes.
- `server/service_settings.py`, `settings_routes.py`: admin-tunable settings (`next_recording` or `restart`).
- `server/telemetry.py`: OpenTelemetry, off unless an OTLP endpoint is set. See `docker/OLLY.md`.
- `webui/`: Svelte 5 + Vite frontend, built into the server image.

## Critical constraints

- **GPU/CUDA only.** CPU fallback exists for ASR only.
- **ASR and diarizer run in separate processes.** Keeps CUDA memory faults contained. Don't merge them.
- **Keep `diarize_worker.py`'s top level free of NeMo/CUDA work.** Load models only inside `diarize_worker()`.
- **Docker torch pinning.** The Dockerfile uses a constraints file so pip can't overwrite the base image's torch. If you see `operator torchvision::nms does not exist`, do a clean rebuild. NumPy must stay `<2.0`.
- **protobuf stays `~=5.29`** (NeMo's pin). Run `pip check` after touching OpenTelemetry packages.
- **No transcript text in logs** unless `LOG_TRANSCRIPTS=1`. Log segments through `log_privacy.segment()`, never the raw text.
- **Config.** App settings live in the root `.env` (see `.env.example`). Compose variables live in `docker/.env`. The HF token is a secret.
- **Scaling.** Several servers can share one Mongo. Each `/listen` connection stays on the server it opened on.
- **Clients.** `mimikiku-capture` and `mimikiku-ios` (sibling folders) stream to `/listen` and use `/api/transcripts`. Keep the WebSocket protocol (2048-sample float chunks at 16 kHz, base64) compatible.
- **iOS sign-in.** Servers with GitHub sign-in sit behind an oauth2-proxy. The iOS app signs in through it first (`OAuthService`, `Server.swift`), then uses a personal API token (`mk_…`). Keep this flow. Hosted-server details are in `LOCAL.md` (not in git).

## Notes

- `pytest.ini` sets `testpaths = server/tests` and a 10 s timeout per test. A test that needs longer is a bug.
- Tests import straight from `server/`. Run pytest from the repo root.
- Session cookie: `mimikiku_session`. API tokens: `mk_…` (capture and iOS only, no account or admin routes).
- Telemetry metrics are named `mimikiku_*` in Prometheus and `mimikiku.*` in OpenTelemetry. Keep label values low-cardinality: never user, transcript or connection IDs. A new metric needs a dashboard panel.
