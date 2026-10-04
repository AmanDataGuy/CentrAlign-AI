import os
import subprocess
import sys
import time

import httpx
import pytest

PORT = 8011


@pytest.fixture(scope="session")
def sandbox(tmp_path_factory):
    url = f"http://127.0.0.1:{PORT}"
    env = {**os.environ, "SANDBOX_DB": str(tmp_path_factory.mktemp("db") / "t.db")}
    proc = subprocess.Popen([sys.executable, "-m", "sandbox", "--port", str(PORT)], env=env)
    for _ in range(50):
        if proc.poll() is not None:  # e.g. port already taken by a previous run's sandbox
            raise RuntimeError(f"sandbox exited at startup (code {proc.returncode}); is port {PORT} in use?")
        try:
            httpx.get(url, timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait(timeout=10)  # free the port before the next pytest run starts its own sandbox


@pytest.fixture
def world(sandbox):
    """Fresh seed per test; returns a setter for chaos flags."""
    httpx.post(f"{sandbox}/_admin/reset")
    return lambda **chaos: httpx.post(f"{sandbox}/_admin/chaos", json=chaos)
