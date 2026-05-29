# Endpoints

Built with **FastAPI** on top of `asyncio` + SQLAlchemy.

- `POST /ingest` — ingests JSON with fields `left_id`, `right_id`, `occurred_at`. Returns `201` with no body.
- `POST /infer` — infers the stats, accepts only field `id`. Returns `200` with the statistics vector.

Both endpoints require an `Authorization` header (API key, not `Bearer`-prefixed) resolved against the database.

# Running

```sh
export DATABASE_URL="postgresql+asyncpg://user:pass@host/db"
uv run serve            # serves on 127.0.0.1:8080 (HOST/PORT/RELOAD env vars override)
```

Interactive docs are available at `/docs` (Swagger) and `/redoc`.

# SQLAlchemy setup

- A single `AsyncEngine` owns the connection pool (`config.py`), created at import and disposed on app shutdown via the FastAPI `lifespan`.
- `async_sessionmaker` produces short-lived sessions; each request gets one through the `get_session` dependency, which rolls back on error and always closes.
- `expire_on_commit=False` avoids implicit blocking refreshes after commit; `pool_pre_ping` + `pool_recycle` keep pooled connections healthy. Pool size is tunable via `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_RECYCLE_SECONDS`.
- Session and `Actor` are passed explicitly into handlers (dependency injection) rather than read from a framework global.

# Observability

Tracing uses **OpenTelemetry** (`observability.py`).

- FastAPI requests and every SQL statement are auto-instrumented, so each trace shows the HTTP span with nested DB query spans.
- `/infer` additionally emits a manual span per statistic — `infer.right_side_count`, `infer.right_side_count_last_7_days`, `infer.first_appeared`, `infer.last_appeared`, `infer.distinct_left_side_count` — all nested under a parent `infer` span. This makes the time to infer each field directly visible.
- By default spans print to the console (no extra infrastructure). Set `OTEL_EXPORTER_OTLP_ENDPOINT` to ship to an OTLP collector (Jaeger/Tempo/Honeycomb); install the exporter with `uv sync --extra otlp`. `OTEL_SERVICE_NAME` overrides the service name.

# Testing

> Tests are AI generated, but reviewed and ran by me

End-to-end tests (spin up a throwaway PostgreSQL in Docker) can be run with:

```sh
uv run test
```
