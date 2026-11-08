"""OpenTelemetry (to Olly, or any OTLP collector): traces, metrics and logs over OTLP/HTTP.

`start(app)` turns it on when OTEL_EXPORTER_OTLP_ENDPOINT is set (Olly's
collector, http://olly-collector:4318 in Docker); unset, nothing is exported
and every instrument below stays a no-op. Instruments can be imported and used
anywhere: the global meter is a proxy that starts recording once `start` runs.

Prometheus names come from the OTel ones: dots become underscores, plus the
unit and _total (mimikiku.transcribe.duration in s ->
mimikiku_transcribe_duration_seconds_bucket). Resource attributes become labels
(service_namespace="mimikiku", service_name, mimikiku_instance). Keep metric
labels low-cardinality: never user ids, emails, transcript or connection ids
(fine in logs and on spans). The Grafana dashboard is in the Olly repo,
config/grafana/dashboards/mimikiku/mimikiku.json; a new or renamed
metric needs its panel there.
"""

import logging
import os
import re
import socket
from typing import Callable, Dict

from opentelemetry import metrics, trace
from opentelemetry.metrics import CallbackOptions, Observation

log = logging.getLogger(__name__)

NAME = "mimikiku"
tracer = trace.get_tracer(NAME)
_providers: list = []  # set by start(); flushed by shutdown()
_meter = metrics.get_meter(NAME)

SECONDS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]
LONG_SECONDS = [1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200]


class _Instruments:
    recordings = _meter.create_counter(
        "mimikiku.recordings",
        unit="{recording}",
        description="/listen connections accepted, by client (session = web app, token = API token) and saved",
    )
    recordings_active = _meter.create_up_down_counter(
        "mimikiku.recordings.active", unit="{recording}", description="Open /listen connections"
    )
    recording_duration = _meter.create_histogram(
        "mimikiku.recording.duration",
        unit="s",
        description="Audio length of finished recordings",
        explicit_bucket_boundaries_advisory=LONG_SECONDS,
    )
    audio = _meter.create_counter("mimikiku.audio", unit="s", description="Audio received for transcription")
    segments = _meter.create_counter("mimikiku.segments", unit="{segment}", description="Transcript lines sent")
    transcribe_duration = _meter.create_histogram(
        "mimikiku.transcribe.duration",
        unit="s",
        description="One pipeline step (about 0.5s of audio): ASR + speaker assignment",
        explicit_bucket_boundaries_advisory=SECONDS,
    )
    model_load_duration = _meter.create_histogram(
        "mimikiku.models.load.duration",
        unit="s",
        description="Getting both models for a new connection (instant when already loaded), by outcome",
        explicit_bucket_boundaries_advisory=[0.01, 0.1, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120],
    )
    refused = _meter.create_counter(
        "mimikiku.listen.refused",
        unit="{connection}",
        description="/listen connections refused, by reason (unauthorized, cross_origin, not_found, models)",
    )
    auth_events = _meter.create_counter(
        "mimikiku.auth.events", unit="{event}", description="Account and admin actions, by action and outcome"
    )
    store_errors = _meter.create_counter(
        "mimikiku.store.errors", unit="{error}", description="Mongo writes that failed while saving recordings, by op"
    )


instruments = _Instruments()


def observe(name: str, unit: str, description: str, read: Callable[[], float | None]) -> None:
    """A gauge read at export time (on the exporter's thread, so `read` may block briefly)."""

    def callback(_: CallbackOptions):
        try:
            value = read()
        except Exception as e:
            log.debug(f"gauge {name}: {e}")
            return []
        return [] if value is None else [Observation(value)]

    _meter.create_observable_gauge(name, callbacks=[callback], unit=unit, description=description)


# Password links carry their secret in the path; keep it out of spans.
_SECRET_PATH = re.compile(r"(/api/auth/links/)[^/?#]+")
_URL_ATTRS = ("url.path", "url.full", "http.target", "http.url")


def _scrub(span, scope: Dict) -> None:
    if span is None or not span.is_recording():
        return
    for key in _URL_ATTRS:
        value = span.attributes.get(key) if span.attributes else None
        if isinstance(value, str) and "/api/auth/links/" in value:
            span.set_attribute(key, _SECRET_PATH.sub(r"\1{secret}", value))


def start(app) -> bool:
    """Export to OTEL_EXPORTER_OTLP_ENDPOINT and instrument the FastAPI app. False when unset."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False

    # Before the instrumentation imports read it: stable HTTP names
    # (http.server.request.duration in seconds, http.route) like our other services.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # Only OTEL_RESOURCE_ATTRIBUTES plus ours: no pid or command line, which
    # would start new metric series on every restart. mimikiku.instance tells
    # horizontally scaled servers apart (MIMIKIKU_INSTANCE, default the hostname).
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME") or "mimikiku-server",
            "service.namespace": NAME,
            "mimikiku.instance": os.environ.get("MIMIKIKU_INSTANCE") or socket.gethostname(),
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    interval = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL") or 15000)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval)],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)
    # uvicorn's loggers don't propagate (listen.py); its access log stays local only.
    logging.getLogger("uvicorn.error").addHandler(handler)

    # /listen is one long-lived socket with a span per message: traced by hand instead.
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls="/listen,/api/health",
        server_request_hook=_scrub,
    )

    _providers[:] = [tracer_provider, meter_provider, logger_provider]
    log.info(f"Telemetry: exporting to {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']} as {resource.attributes['service.name']}")
    return True


def shutdown() -> None:
    """Flush and stop exporting. Call last on app shutdown (listen.py's lifespan)."""
    while _providers:
        try:
            _providers.pop().shutdown()
        except Exception:
            pass
