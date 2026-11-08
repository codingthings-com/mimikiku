# Telemetry (OpenTelemetry)

Mimikiku can export traces, metrics and logs over OTLP/HTTP. It's **off by default**. Without a collector, nothing is exported.

"Olly" is the observability stack this was built against: an OpenTelemetry Collector in front of Prometheus (metrics), Loki (logs) and Tempo (traces), with Grafana. Mimikiku doesn't need it. Any OTLP/HTTP collector works.

## Option 1: local stack on `olly_network`

If a Docker network named `olly_network` exists, `docker-start.sh` adds [`compose.olly.yml`](compose.olly.yml). The server joins that network and exports to `http://olly-collector:4318`.

To try it with Grafana's all-in-one image:

```bash
docker network create olly_network
docker run -d --name olly-collector --network olly_network -p 3000:3000 grafana/otel-lgtm
./docker-start.sh     # prints "Telemetry on: sending to olly-collector ..."
```

Grafana is at http://localhost:3000 (admin / admin). This setup is for testing. For anything long-lived, run the components separately with persistent storage.

To turn telemetry off while `olly_network` exists, set `OLLY_OTLP_URL=` (empty) in `docker/.env`. For a collector with another name, set `OLLY_OTLP_URL=http://<name>:4318`.

## Option 2: any other collector

Set the endpoint in `docker/.env`:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector.example.internal:4318
```

Use a hostname that resolves inside Docker, not `localhost`. Put it in `docker/.env`, not the root `.env`, because compose's `environment:` overrides the root file. Other standard `OTEL_*` variables (for example `OTEL_EXPORTER_OTLP_HEADERS`) can go in the root `.env`.

## What gets exported

**Resource attributes** on everything: `service.name` (`mimikiku-server`), `service.namespace=mimikiku`, `mimikiku.instance` (`MIMIKIKU_INSTANCE`), and `deployment.environment.name` (`DEPLOY_ENV`, default `production`).

**Traces:** every HTTP API request, plus a `listen.connect` span per WebSocket (auth through models ready). `/api/health` isn't traced. Secrets in password-link URLs are scrubbed.

**Logs:** the server's Python logs at INFO and above.

**Metrics:** exported every 15 s. In Prometheus, dots become underscores and the unit is appended. For example, `mimikiku.transcribe.duration` becomes `mimikiku_transcribe_duration_seconds_bucket`.

| Metric | Type | What it measures |
|---|---|---|
| `mimikiku.recordings` | counter | `/listen` connections accepted, by client and whether saved |
| `mimikiku.recordings.active` | up/down counter | Open `/listen` connections |
| `mimikiku.recording.duration` | histogram (s) | Length of finished recordings |
| `mimikiku.audio` | counter (s) | Audio received for transcription |
| `mimikiku.segments` | counter | Transcript lines sent |
| `mimikiku.transcribe.duration` | histogram (s) | One pipeline step (about 0.5 s of audio) |
| `mimikiku.models.load.duration` | histogram (s) | Getting both models for a new connection, by outcome |
| `mimikiku.listen.refused` | counter | Refused `/listen` connections, by reason |
| `mimikiku.auth.events` | counter | Account and admin actions, by outcome |
| `mimikiku.store.errors` | counter | Failed Mongo writes while saving recordings |
| `mimikiku.gpu.memory.used` / `.total` | gauge (bytes) | GPU memory in use and total |
| `mimikiku.ram.used` | gauge (bytes) | Host RAM in use |
| `http.server.request.duration` | histogram (s) | HTTP API latency, by route and status |

Labels never include user, transcript or connection IDs.

Without a collector, the admin Monitoring page in the web app still shows model status and performance counters.

## Grafana dashboard

[`grafana-dashboard-mimikiku-example.json`](grafana-dashboard-mimikiku-example.json) covers health, recordings, transcription speed, models and GPU, accounts, logs, and slow or failed traces. Import it in Grafana (Dashboards → New → Import), then pick your Prometheus, Loki and Tempo datasources. Panels for a backend you don't run stay empty.

The dashboard filters on resource attributes as labels (`service_namespace`, `deployment_environment_name`, `mimikiku_instance`). Your pipeline has to turn them into labels:

- **OpenTelemetry Collector with Prometheus remote write:** set `resource_to_telemetry_conversion: enabled: true` on the `prometheusremotewrite` exporter. Olly does this.
- **Prometheus's own OTLP receiver** (used by `grafana/otel-lgtm`): list the attributes under `otlp: promote_resource_attributes`. `otel-lgtm` promotes `service.namespace` and `deployment.environment.name` but not `mimikiku.instance`, so the Instance dropdown stays empty. "All" still shows every server.
