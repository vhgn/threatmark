import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid

import asyncpg


POSTGRES_IMAGE = os.environ.get("TEST_POSTGRES_IMAGE", "postgres:16")
POSTGRES_DATABASE = "threatmark_test"
POSTGRES_PASSWORD = "postgres"
POSTGRES_USER = "postgres"


def main() -> int:
    test_database_url = os.environ.get("TEST_DATABASE_URL")

    if test_database_url:
        return _run_unittest(test_database_url)

    container_name = f"threatmark-test-postgres-{uuid.uuid4().hex[:12]}"

    try:
        host_port = _start_postgres(container_name)
        test_database_url = (
            f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
            f"@127.0.0.1:{host_port}/{POSTGRES_DATABASE}"
        )
        asyncio.run(_wait_for_postgres(host_port))
        return _run_unittest(test_database_url)
    finally:
        _stop_postgres(container_name)


def _start_postgres(container_name: str) -> int:
    print(f"Starting PostgreSQL Docker container {container_name}...", flush=True)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-e",
            f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
            "-e",
            f"POSTGRES_DB={POSTGRES_DATABASE}",
            "-p",
            "127.0.0.1::5432",
            POSTGRES_IMAGE,
        ],
        check=True,
    )

    port_output = subprocess.run(
        ["docker", "port", container_name, "5432/tcp"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    return int(port_output.rsplit(":", maxsplit=1)[1])


async def _wait_for_postgres(host_port: int) -> None:
    deadline = time.monotonic() + 30
    last_error: BaseException | None = None

    while time.monotonic() < deadline:
        try:
            connection = await asyncpg.connect(
                host="127.0.0.1",
                port=host_port,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                database=POSTGRES_DATABASE,
            )
            await connection.close()
            return
        except (asyncpg.PostgresError, OSError, socket.error) as error:
            last_error = error
            await asyncio.sleep(0.25)

    raise RuntimeError("PostgreSQL did not become ready within 30 seconds") from last_error


def _run_unittest(test_database_url: str) -> int:
    env = os.environ.copy()
    env["TEST_DATABASE_URL"] = test_database_url

    print("Running backend tests...", flush=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "threatmark_backend_test",
            "-v",
        ],
        env=env,
    )
    return result.returncode


def _stop_postgres(container_name: str) -> None:
    subprocess.run(
        ["docker", "stop", container_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
