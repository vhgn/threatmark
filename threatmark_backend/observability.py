"""OpenTelemetry tracing setup for the Threatmark backend.

Tracing is configured once at process start via :func:`configure_tracing` and the
ASGI app / database engine are wired up with :func:`instrument`. By default spans
are printed to the console so per-request (and per-field) timings are visible with
zero extra infrastructure. Set ``OTEL_EXPORTER_OTLP_ENDPOINT`` to ship spans to an
OTLP collector (Jaeger, Tempo, Honeycomb, ...) instead.
"""

import os

from fastapi import FastAPI

from sqlalchemy.ext.asyncio import AsyncEngine
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "threatmark-backend")

_configured = False


def configure_tracing() -> TracerProvider:
    """Install a global :class:`TracerProvider`. Idempotent."""
    global _configured

    provider = trace.get_tracer_provider()
    if _configured and isinstance(provider, TracerProvider):
        return provider

    provider = TracerProvider(
        resource=Resource.create({"service.name": SERVICE_NAME})
    )

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        # Batch in production so exporting never blocks the request path.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # pyright: ignore[reportMissingImports]
            OTLPSpanExporter,
        )

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        # Simple processor flushes synchronously so spans appear immediately
        # in the console during local development.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _configured = True
    return provider


def instrument(app: FastAPI, engine: AsyncEngine) -> None:
    """Auto-instrument the FastAPI app and the SQLAlchemy engine.

    This produces a span per HTTP request and a child span per emitted SQL
    statement, which nest under the manual per-field spans created in
    ``handler.infer``.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    # Async engines wrap a sync engine that the instrumentor hooks into.
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
