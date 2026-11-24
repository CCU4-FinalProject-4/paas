import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

BASE_URL = os.getenv("WEB_URL", "http://localhost:8080")


def wait_healthy(container, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if status == "healthy":
            return
        time.sleep(1)
    raise TimeoutError(f"{container} is not healthy")


@pytest.fixture
def web():
    with httpx.Client(base_url=f"{BASE_URL}/api", timeout=10) as client:
        yield client


def test_load_balancer_uses_both_replicas(web):
    served = {web.get("/health").json()["served_by"] for _ in range(20)}
    assert served == {"api-1", "api-2"}


def test_session_survives_replica_stop(web):
    first = web.get("/session").json()
    replica = first["served_by"]
    subprocess.run(["docker", "stop", f"paas-{replica}"], check=True, capture_output=True)
    try:
        second = web.get("/session").json()
    finally:
        subprocess.run(["docker", "start", f"paas-{replica}"], check=True, capture_output=True)
        wait_healthy(f"paas-{replica}")

    assert second["served_by"] != replica
    assert second["new"] is False
    assert second["session"]["session_id"] == first["session"]["session_id"]
    assert second["session"]["views"] == first["session"]["views"] + 1


def test_concurrent_increments_are_not_lost(web):
    start = web.get("/visits").json()["visits"]

    def visit(_):
        r = web.post("/visits")
        r.raise_for_status()
        return r.json()

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(visit, range(200)))

    values = [r["visits"] for r in results]
    assert len(set(values)) == 200
    assert web.get("/visits").json()["visits"] == start + 200
    assert {r["served_by"] for r in results} == {"api-1", "api-2"}
