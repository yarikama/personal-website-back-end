"""
OpenTelemetry tracing: one trace per request, with spans for the database,
Redis and the autocomplete model (httpx), exported over OTLP/HTTP to Grafana
Tempo. Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (the exporter's own
standard variable, e.g. http://tempo.monitoring:4318).
"""

import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from sqlalchemy.ext.asyncio import AsyncEngine

# Probes hit /health every few seconds; traces of them are only noise.
EXCLUDED_URLS = "health"


def instrument(
    app: FastAPI, provider: TracerProvider, engine: AsyncEngine | None = None
) -> None:
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, excluded_urls=EXCLUDED_URLS
    )
    if engine is not None:
        # The instrumentation hooks the sync engine that the async one wraps.
        SQLAlchemyInstrumentor().instrument(
            engine=engine.sync_engine, tracer_provider=provider
        )
    RedisInstrumentor().instrument(tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)


def setup_tracing(
    app: FastAPI, engine: AsyncEngine, exporter: SpanExporter | None = None
) -> bool:
    """Instrument the app when an OTLP endpoint is configured."""
    if exporter is None:
        if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            return False
        exporter = OTLPSpanExporter()
    # OTEL_SERVICE_NAME, when set, wins over this default.
    resource = Resource.create(
        {"service.name": os.environ.get("OTEL_SERVICE_NAME", "website-backend")}
    )
    provider = TracerProvider(resource=resource)
    # Batches spans in a background thread; requests never wait on Tempo.
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    instrument(app, provider, engine)
    return True
