"""Telemetry stays off without an endpoint, and keeps password-link secrets out of spans."""

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

import telemetry


def test_start_is_a_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.start(FastAPI()) is False
    # Instruments are usable regardless.
    telemetry.instruments.segments.add(1)
    telemetry.instruments.transcribe_duration.record(0.2)


def test_scrub_hides_password_link_secret():
    span = TracerProvider().get_tracer("t").start_span(
        "GET",
        attributes={
            "url.path": "/api/auth/links/s3cret-value",
            "url.full": "http://h/api/auth/links/s3cret-value?x=1",
            "http.route": "/api/auth/links/{secret}",
        },
    )
    telemetry._scrub(span, {})
    assert span.attributes["url.path"] == "/api/auth/links/{secret}"
    assert span.attributes["url.full"] == "http://h/api/auth/links/{secret}?x=1"
    span.end()


def test_scrub_leaves_other_paths():
    span = TracerProvider().get_tracer("t").start_span("GET", attributes={"url.path": "/api/transcripts"})
    telemetry._scrub(span, {})
    assert span.attributes["url.path"] == "/api/transcripts"
    span.end()


def test_start_and_shutdown_on_this_fastapi():
    """start() must work on the installed FastAPI/Starlette (it once used a removed API).

    In a subprocess: it installs global providers and a root log handler.
    """
    import os
    import subprocess
    import sys

    code = (
        "import telemetry, fastapi;"
        "app = fastapi.FastAPI();"
        "assert telemetry.start(app);"
        "telemetry.instruments.segments.add(1);"
        "telemetry.shutdown();"
        "print('ok')"
    )
    env = {
        **os.environ,
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",  # nothing listens; exports just fail
        "OTEL_EXPORTER_OTLP_TIMEOUT": "1",
    }
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", code], cwd=server_dir, env=env, capture_output=True, text=True, timeout=9)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("ok")
