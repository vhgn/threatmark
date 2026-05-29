import argparse
import asyncio
import json
import random
import string
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any, Mapping


ASCII_ALPHABET = string.ascii_letters + string.digits


@dataclass(frozen=True)
class RequestResult:
    endpoint: str
    status: int | None
    latency_ms: float
    ok: bool
    error: str | None = None


@dataclass
class Metrics:
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    concurrency: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    requests_per_second: float
    success_rate: float
    status_counts: dict[str, int]
    error_counts: dict[str, int]
    latency_ms: dict[str, float | None]
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)


def main() -> int:
    args = _parse_args()
    metrics = asyncio.run(
        run_stress_test(
            base_url=args.url,
            api_key=args.api_key,
            duration_seconds=args.duration_seconds,
            concurrency=args.concurrency,
            ingest_ratio=args.ingest_ratio,
            right_id_pool_size=args.right_id_pool_size,
            timeout_seconds=args.timeout_seconds,
        )
    )
    _print_report(metrics, as_json=args.json)
    return 0 if metrics.failed_requests == 0 else 1


async def run_stress_test(
    *,
    base_url: str,
    api_key: str,
    duration_seconds: float,
    concurrency: int,
    ingest_ratio: float,
    right_id_pool_size: int,
    timeout_seconds: float,
) -> Metrics:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    if not 0 <= ingest_ratio <= 1:
        raise ValueError("ingest-ratio must be between 0 and 1")

    base_url = base_url.rstrip("/")
    deadline = time.perf_counter() + duration_seconds
    results: list[RequestResult] = []
    lock = asyncio.Lock()
    right_ids = [_member_id() for _ in range(max(1, right_id_pool_size))]

    async def worker(worker_index: int) -> None:
        rng = random.Random(time.time_ns() + worker_index)

        while time.perf_counter() < deadline:
            if rng.random() < ingest_ratio:
                endpoint = "/ingest"
                payload = _ingest_payload(rng, right_ids)
            else:
                endpoint = "/infer"
                payload: Mapping[str, object] = {"id": rng.choice(right_ids)}

            result = await asyncio.to_thread(
                _post_json,
                f"{base_url}{endpoint}",
                api_key,
                payload,
                endpoint,
                timeout_seconds,
            )

            async with lock:
                results.append(result)

    started_at = datetime.now(UTC)
    started_monotonic = time.perf_counter()
    await asyncio.gather(*(worker(index) for index in range(concurrency)))
    duration = time.perf_counter() - started_monotonic
    finished_at = datetime.now(UTC)

    return _summarize(
        results=results,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration,
        concurrency=concurrency,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress test Threatmark HTTP API endpoints.")
    parser.add_argument("--url", required=True, help="Base API URL, for example http://127.0.0.1:8080")
    parser.add_argument("--api-key", required=True, help="Authorization API key")
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--ingest-ratio", type=float, default=0.9)
    parser.add_argument("--right-id-pool-size", type=int, default=1000)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    return parser.parse_args()


def _post_json(
    url: str,
    api_key: str,
    payload: Mapping[str, object],
    endpoint: str,
    timeout_seconds: float,
) -> RequestResult:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    started = time.perf_counter()

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read()
            status = response.status
            ok = 200 <= status < 300
            error_text = None if ok else f"http_{status}"
    except urllib.error.HTTPError as http_error:
        status = http_error.code
        http_error.read()
        ok = False
        error_text = f"http_{status}"
    except Exception as exc:
        status = None
        ok = False
        error_text = type(exc).__name__

    return RequestResult(
        endpoint=endpoint,
        status=status,
        latency_ms=(time.perf_counter() - started) * 1000,
        ok=ok,
        error=error_text,
    )


def _ingest_payload(rng: random.Random, right_ids: list[str]) -> dict[str, object]:
    occurred_at = datetime.now(UTC) - timedelta(seconds=rng.randint(0, 10 * 24 * 60 * 60))
    return {
        "left_id": _member_id(rng),
        "right_id": rng.choice(right_ids),
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
    }


def _member_id(rng: random.Random | None = None) -> str:
    if rng is None:
        return "".join(random.choice(ASCII_ALPHABET) for _ in range(16))

    return "".join(rng.choice(ASCII_ALPHABET) for _ in range(16))


def _summarize(
    *,
    results: list[RequestResult],
    started_at: datetime,
    finished_at: datetime,
    duration_seconds: float,
    concurrency: int,
) -> Metrics:
    status_counts = Counter(str(result.status) for result in results)
    error_counts = Counter(result.error for result in results if result.error)
    successful = sum(1 for result in results if result.ok)
    failed = len(results) - successful

    endpoint_metrics = {
        endpoint: _summarize_subset(
            [result for result in results if result.endpoint == endpoint],
            duration_seconds=duration_seconds,
        )
        for endpoint in sorted({result.endpoint for result in results})
    }

    return Metrics(
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        concurrency=concurrency,
        total_requests=len(results),
        successful_requests=successful,
        failed_requests=failed,
        requests_per_second=len(results) / duration_seconds if duration_seconds else 0,
        success_rate=successful / len(results) if results else 0,
        status_counts=dict(sorted(status_counts.items())),
        error_counts={str(key): value for key, value in sorted(error_counts.items())},
        latency_ms=_latency_summary([result.latency_ms for result in results]),
        endpoints=endpoint_metrics,
    )


def _summarize_subset(results: list[RequestResult], *, duration_seconds: float) -> dict[str, Any]:
    successful = sum(1 for result in results if result.ok)
    failed = len(results) - successful

    return {
        "total_requests": len(results),
        "successful_requests": successful,
        "failed_requests": failed,
        "requests_per_second": len(results) / duration_seconds if duration_seconds else 0,
        "success_rate": successful / len(results) if results else 0,
        "status_counts": dict(sorted(Counter(str(result.status) for result in results).items())),
        "error_counts": {
            str(key): value
            for key, value in sorted(Counter(result.error for result in results if result.error).items())
        },
        "latency_ms": _latency_summary([result.latency_ms for result in results]),
    }


def _latency_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "min": None,
            "avg": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
        }

    return {
        "min": min(values),
        "avg": mean(values),
        "p50": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
        "max": max(values),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _print_report(metrics: Metrics, *, as_json: bool) -> None:
    payload = {
        "started_at": metrics.started_at.isoformat(),
        "finished_at": metrics.finished_at.isoformat(),
        "duration_seconds": metrics.duration_seconds,
        "concurrency": metrics.concurrency,
        "total_requests": metrics.total_requests,
        "successful_requests": metrics.successful_requests,
        "failed_requests": metrics.failed_requests,
        "requests_per_second": metrics.requests_per_second,
        "success_rate": metrics.success_rate,
        "status_counts": metrics.status_counts,
        "error_counts": metrics.error_counts,
        "latency_ms": metrics.latency_ms,
        "endpoints": metrics.endpoints,
    }

    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print("Threatmark API stress test results")
    print(f"Window: {payload['started_at']} -> {payload['finished_at']}")
    print(f"Duration: {metrics.duration_seconds:.2f}s")
    print(f"Concurrency: {metrics.concurrency}")
    print(f"Requests: {metrics.total_requests} total, {metrics.successful_requests} ok, {metrics.failed_requests} failed")
    print(f"Throughput: {metrics.requests_per_second:.2f} req/s")
    print(f"Success rate: {metrics.success_rate * 100:.2f}%")
    print(f"Status counts: {metrics.status_counts}")
    print(f"Error counts: {metrics.error_counts}")
    print(f"Latency ms: {_format_latency(metrics.latency_ms)}")

    for endpoint, endpoint_metrics in metrics.endpoints.items():
        print(f"{endpoint}:")
        print(
            "  "
            f"{endpoint_metrics['total_requests']} total, "
            f"{endpoint_metrics['successful_requests']} ok, "
            f"{endpoint_metrics['failed_requests']} failed, "
            f"{endpoint_metrics['requests_per_second']:.2f} req/s, "
            f"success {endpoint_metrics['success_rate'] * 100:.2f}%"
        )
        print(f"  status counts: {endpoint_metrics['status_counts']}")
        print(f"  latency ms: {_format_latency(endpoint_metrics['latency_ms'])}")


def _format_latency(values: dict[str, float | None]) -> str:
    parts = []
    for key in ("min", "avg", "p50", "p90", "p95", "p99", "max"):
        value = values[key]
        parts.append(f"{key}={value:.2f}" if value is not None else f"{key}=n/a")
    return ", ".join(parts)
