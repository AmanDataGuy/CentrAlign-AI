"""Web console for the worker:  python -m worker.console   ->  http://127.0.0.1:8000

Start a task (or a free-text goal), toggle chaos, watch every decision live, answer approvals/questions in the
browser, and see the independent verification + evidence. Same core as the CLI: only the approver/ask_human differ.
ponytail: one run at a time, polling instead of SSE -- enough for a local operator console.
"""
import base64
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from . import llm as llm_mod
from .__main__ import load_dotenv
from .loop import run_task
from .models import RunState, TaskSpec
from .spend import Ledger

RUNS, TASKS = Path("runs"), Path("tasks")
PORT = int(os.environ.get("PORT", "8000"))                  # hosting platforms inject PORT
EMBED = os.environ.get("EMBED_SANDBOX") == "1"              # deployed: one process serves console + sandbox
SANDBOX = os.environ.get("SANDBOX_URL") or (f"http://127.0.0.1:{PORT}" if EMBED else "http://127.0.0.1:8001")
SERVER = bool(os.environ.get("RENDER"))                     # headless host: no display for a visible browser
GUARDED = ("/api/tasks", "/api/spend", "/api/runs", "/runs/", "/_admin")   # everything except the sandbox's own pages
app = FastAPI(title="Task worker console")
busy = threading.Lock()
live: dict[str, "Bridge"] = {}


INTERNAL = secrets.token_hex(16)            # how the console's own calls identify themselves (never an IP address)
INTERNAL_HEADERS = {"x-internal": INTERNAL}


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """If CONSOLE_PASSWORD is set, the console and the sandbox admin routes need HTTP Basic auth (any username).
    The sandbox's mock apps stay open. Nothing is trusted because of where it comes from: the agent's own browser
    also connects from this machine, so only the per-process token (or the password) gets through."""
    pw, path = os.environ.get("CONSOLE_PASSWORD"), request.url.path
    guarded = path == "/" or path.startswith(GUARDED)
    internal = secrets.compare_digest(request.headers.get("x-internal", "").encode(), INTERNAL.encode())
    if pw and guarded and not internal:
        auth = request.headers.get("authorization", "")
        try:
            given = base64.b64decode(auth[6:]).decode().partition(":")[2] if auth.startswith("Basic ") else ""
        except ValueError:
            given = ""
        if not secrets.compare_digest(given.encode(), pw.encode()):
            return PlainTextResponse("Authentication required", 401,
                                     headers={"WWW-Authenticate": 'Basic realm="Task worker console"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


class Bridge:
    """Hands approvals/questions from the agent thread to the browser and blocks until a human answers."""

    def __init__(self):
        self.pending, self.answer, self.event, self.result = None, None, threading.Event(), None

    def _wait(self, pending: dict, timeout=900):
        self.pending, self.answer = pending, None
        self.event.clear()
        answered = self.event.wait(timeout)
        self.pending = None
        return self.answer if answered else None

    def approver(self, req: dict):
        a = self._wait({"kind": "approval", **req})
        return (a["approved"], a.get("note") or ("approved in console" if a["approved"] else "denied in console")) \
            if a else (False, "no answer within 15 minutes -- denied")

    def ask_human(self, question: str) -> str:
        a = self._wait({"kind": "question", "question": question})
        return a["text"] if a else "No answer from the human. Do the safest thing or finish as blocked."


class RunRequest(BaseModel):
    task: str
    goal: str = ""
    params: dict[str, str] = {}
    chaos: dict[str, int] = {}
    headed: bool = False


class Answer(BaseModel):
    approved: bool = False
    note: str = ""
    text: str = ""


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "console.html").read_text(encoding="utf-8")


@app.get("/api/tasks")
def tasks():
    out = []
    for f in sorted(TASKS.glob("*.yaml"), key=lambda f: (f.name != "invoice_to_erp.yaml", f.name)):  # brief example first
        s = TaskSpec.load(f)
        out.append({"file": f.name, "id": s.id, "goal": s.goal_text, "params": s.params,
                    "allowed_writes": s.allowed_writes, "checks": [c.get("name", c["api"]) for c in s.checks]})
    return out


@app.get("/api/spend")
def spend():
    led = Ledger()
    return {"spent": led.total(), "budget": led.budget, "model": os.environ.get("WORKER_MODEL", ""), "server": SERVER}


@app.post("/api/runs")
def start(req: RunRequest):
    if not busy.acquire(blocking=False):
        raise HTTPException(409, "A run is already in progress.")
    try:
        spec = TaskSpec.load(TASKS / Path(req.task).name, req.params)
        if req.goal.strip() and req.goal.strip() != spec.goal_text:
            spec.goal = req.goal.strip().replace("{", "{{").replace("}", "}}")
        httpx.post(f"{SANDBOX}/_admin/reset", headers=INTERNAL_HEADERS, timeout=10)
        if active := {k: v for k, v in req.chaos.items() if v}:
            httpx.post(f"{SANDBOX}/_admin/chaos", json=active, headers=INTERNAL_HEADERS, timeout=10)
        llm = llm_mod.from_env()
    except Exception as e:
        busy.release()
        raise HTTPException(400, f"Could not start: {e}")
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    bridge = live[run_id] = Bridge()
    os.environ["WORKER_HEADED"] = "1" if req.headed and not SERVER else "0"

    def work():
        try:
            state, verdict, _ = run_task(spec, llm, bridge.approver, bridge.ask_human, sandbox_url=SANDBOX,
                                         runs_dir=RUNS, run_id=run_id)
            bridge.result = {"verdict": verdict and {"ok": verdict.ok, "checks": verdict.checks}}
        except Exception as e:  # surfaced in the UI, never silently lost
            bridge.result = {"error": repr(e)}
        finally:
            busy.release()

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
def poll(run_id: str, after: int = 0):
    d = RUNS / Path(run_id).name
    lines = (d / "trace.jsonl").read_text(encoding="utf-8").splitlines() if (d / "trace.jsonl").exists() else []
    events = []
    for line in lines[after:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:  # line still being written: pick it up next poll
            break
    bridge = live.get(run_id)
    state = None
    for _ in range(10):  # state.json is replaced atomically; on Windows a read can land mid-replace
        try:
            state = RunState.load(d) if (d / "state.json").exists() else None
            break
        except (OSError, ValueError):
            time.sleep(0.03)
    return {"events": events, "next": after + len(events),
            "pending": bridge and bridge.pending, "result": bridge and bridge.result,
            "state": state and {"status": state.status, "outcome": state.outcome, "step": state.step,
                                "cost": round(state.cost_usd, 4), "plan": state.plan, "facts": state.facts},
            "evidence": sorted(p.name for p in d.glob("*.png"))}


@app.post("/api/runs/{run_id}/answer")
def answer(run_id: str, a: Answer):
    bridge = live.get(run_id)
    if not bridge or not bridge.pending:
        raise HTTPException(404, "Nothing is waiting for an answer.")
    bridge.answer = a.model_dump()
    bridge.event.set()
    return {"ok": True}


@app.get("/runs/{run_id}/{name}")
def run_file(run_id: str, name: str):
    p = RUNS / Path(run_id).name / Path(name).name  # names only: no path traversal
    if p.suffix not in (".png", ".md") or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


def embed_sandbox():
    """Serve the mock company from this same app (after every console route, so the console's '/' wins)."""
    from sandbox.app import app as sandbox_app
    app.mount("/", sandbox_app)


if __name__ == "__main__":
    load_dotenv()
    if EMBED:
        from sandbox import db
        db.reset()
        embed_sandbox()
    print(f"Task worker console on port {PORT}  (sandbox at {SANDBOX}{' - embedded' if EMBED else ''})")
    uvicorn.run(app, host="0.0.0.0" if "PORT" in os.environ else "127.0.0.1", port=PORT, log_level="warning",
                proxy_headers=False)
