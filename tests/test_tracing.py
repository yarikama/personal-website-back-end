import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import text

from core import tracing
from core.logging import add_trace_id
from core.tracing import instrument, setup_tracing
from db.session import engine


@pytest.fixture
def traced():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    seen = {}
    app = FastAPI()

    @app.get("/api/v1/projects/{slug}")
    def project(slug: str):
        record = {"extra": {}}
        add_trace_id(record)
        seen["log"] = record["extra"]["trace"]
        return {"slug": slug}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    instrument(app, provider)
    yield TestClient(app), exporter, seen
    # Both patch their libraries globally; leave other tests untraced.
    RedisInstrumentor().uninstrument()
    HTTPXClientInstrumentor().uninstrument()


def test_a_request_becomes_a_trace_named_after_its_route(traced):
    client, exporter, _ = traced

    client.get("/api/v1/projects/papit")

    server = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    assert [s.name for s in server] == ["GET /api/v1/projects/{slug}"]
    assert server[0].attributes["http.route"] == "/api/v1/projects/{slug}"


def test_logs_inside_a_request_carry_its_trace_id(traced):
    client, exporter, seen = traced

    client.get("/api/v1/projects/papit")

    trace_id = exporter.get_finished_spans()[0].context.trace_id
    assert seen["log"] == f" trace_id={trace_id:032x}"


def test_logs_outside_a_request_have_no_trace_id():
    record = {"extra": {}}

    add_trace_id(record)

    assert record["extra"]["trace"] == ""


def test_health_probes_are_not_traced(traced):
    client, exporter, _ = traced

    client.get("/health")

    assert exporter.get_finished_spans() == ()


def test_tracing_is_off_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    assert setup_tracing(FastAPI(), engine) is False


@pytest.mark.anyio
async def test_queries_through_the_async_engine_are_traced(monkeypatch):
    # The span is exported as it ends, rather than by a background batch.
    monkeypatch.setattr(tracing, "BatchSpanProcessor", SimpleSpanProcessor)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    exporter = InMemorySpanExporter()
    try:
        assert setup_tracing(FastAPI(), engine, exporter=exporter) is True
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        SQLAlchemyInstrumentor().uninstrument()
        RedisInstrumentor().uninstrument()
        HTTPXClientInstrumentor().uninstrument()
        await engine.dispose()

    [span] = [s for s in exporter.get_finished_spans() if s.name.startswith("SELECT")]
    assert span.resource.attributes["service.name"] == "website-backend"


def test_only_our_exporter_sends_telemetry():
    """
    With OTEL_EXPORTER_OTLP_ENDPOINT set, FastAPI would add exporters of its
    own: a second one for traces, and metrics and logs, which Tempo does not
    take. In a fresh process, as the provider globals can be set only once.
    """
    script = """
from main import app
from fastapi.testclient import TestClient
from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider

with TestClient(app):
    pass
processors = trace.get_tracer_provider()._active_span_processor._span_processors
print(len(processors), type(metrics.get_meter_provider()).__name__,
      type(get_logger_provider()).__name__)
"""
    env = {
        **os.environ,
        # Nothing listens there; nothing is sent before the process ends.
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        "OTEL_BSP_SCHEDULE_DELAY": "600000",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent.parent / "app",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    processors, meter_provider, logger_provider = result.stdout.split()
    assert processors == "1"
    assert meter_provider.startswith("_Proxy")
    assert logger_provider.startswith("Proxy")
