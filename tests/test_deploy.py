"""Hosting behaviour: password gate, health check, sandbox served from the same app."""
import base64

from fastapi.testclient import TestClient

from worker import console


def _basic(pw):
    return {"authorization": "Basic " + base64.b64encode(f"anyone:{pw}".encode()).decode()}


def test_password_gate(monkeypatch):
    monkeypatch.setenv("CONSOLE_PASSWORD", "s3cret")
    c = TestClient(console.app)
    assert c.get("/healthz").status_code == 200                       # platform health check stays open
    for path in ("/", "/api/spend", "/api/tasks", "/_admin/reset"):
        assert c.get(path).status_code == 401
    assert c.post("/api/runs", json={"task": "invoice_to_erp.yaml"}).status_code == 401  # cannot start (spend) runs
    assert c.get("/api/spend", headers=_basic("wrong")).status_code == 401
    assert c.get("/api/spend", headers=_basic("s3cret")).status_code == 200
    assert c.get("/", headers=_basic("s3cret")).status_code == 200
    # the worker's own calls to its sandbox come from loopback and need no credentials
    assert TestClient(console.app, client=("127.0.0.1", 5000)).get("/api/spend").status_code == 200


def test_no_password_means_open_for_local_use(monkeypatch):
    monkeypatch.delenv("CONSOLE_PASSWORD", raising=False)
    assert TestClient(console.app).get("/api/spend").status_code == 200


def test_sandbox_embedded_in_console(monkeypatch):
    monkeypatch.delenv("CONSOLE_PASSWORD", raising=False)
    console.embed_sandbox()
    c = TestClient(console.app)
    assert "Task Worker Console" in c.get("/").text                    # console keeps '/'
    assert c.get("/erp/login").status_code == 200                      # mock company on the same port
    assert c.get("/portal/login").status_code == 200
