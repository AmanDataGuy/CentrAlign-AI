"""Console end-to-end: start a run over HTTP, the agent hits an approval, a 'human' approves via the API, verified."""
import time

from fastapi.testclient import TestClient

from tests.test_e2e import Driver, login, ref
from worker import console
from worker.llm import ScriptedLLM


class BankDriver(Driver):
    def decide(self, obs):
        if not obs or 'heading "Bills"' in obs:
            return [("browser_open", {"url": "/erp/vendors/2"})]
        if "Sign in to AcmeBooks" in obs:
            return login(obs, "ops", "erp-demo")
        if 'heading "Vendor: Globex Corp"' in obs:
            return [("browser_fill", {"ref": ref(obs, "textbox", "New bank account"), "text": "ACCT-002-GLX-7781"}),
                    ("browser_click", {"ref": ref(obs, "button", "Update bank details")})]
        if "Bank details updated" in obs:
            return [("finish", {"summary": "Globex bank account updated to ACCT-002-GLX-7781."})]
        return [("finish", {"summary": "could not complete", "status": "blocked"})]


def _wait(c, run_id, key, timeout=90):
    """Poll the run until d[key] is set; on timeout fail with the whole poll payload, not a TypeError."""
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/runs/{run_id}").json()
        if d[key]:
            return d
        if key == "pending" and d["result"]:  # the run ended without ever asking
            break
        time.sleep(0.1)
    raise AssertionError(f"no {key!r} within {timeout}s; last poll: {d}")


def test_console_run_with_browser_approval(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(console, "SANDBOX", sandbox)
    monkeypatch.setattr(console, "RUNS", tmp_path)
    monkeypatch.setattr(console.llm_mod, "from_env", lambda: ScriptedLLM(BankDriver()))
    c = TestClient(console.app)
    assert any(t["id"] == "vendor_bank_update" for t in c.get("/api/tasks").json())
    run_id = c.post("/api/runs", json={"task": "vendor_bank_update.yaml"}).json()["run_id"]
    d = _wait(c, run_id, "pending")
    assert d["pending"]["kind"] == "approval" and "Update bank details" in d["pending"]["desc"]
    assert c.post(f"/api/runs/{run_id}/answer", json={"approved": True}).json()["ok"]
    d = _wait(c, run_id, "result")
    assert d["state"]["status"] == "done" and d["result"]["verdict"]["ok"]
    assert "final.png" in d["evidence"]
