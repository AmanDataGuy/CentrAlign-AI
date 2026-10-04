"""The two pieces of data the core runs on: a TaskSpec (what to do, as data) and RunState (durable progress)."""
import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


def atomic_write(path: Path, text: str):
    """Temp file + rename. On Windows the rename fails while a reader (the console's polling) has the target open,
    so retry briefly instead of crashing the run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(40):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.025)


class RiskRule(BaseModel):
    tool: str = "*"          # glob on tool name
    match: str               # regex on JSON-encoded args
    reason: str
    action: Literal["ask", "deny"] = "ask"


class TaskSpec(BaseModel):
    """A task is data. New workflow = new YAML (and maybe a new tool), never new agent code."""
    id: str
    goal: str                                 # may contain {param} placeholders
    params: dict[str, str] = {}
    context: str = ""                         # company SOP / procedures, injected as trusted context
    allowed_tools: list[str]
    risk_rules: list[RiskRule] = []           # task-specific approval rules (on top of policy defaults)
    allowed_writes: list[str] = []            # form-POST paths this task may submit without asking (globs)
    truth: dict[str, dict] = {}               # verifier: ground truth queries against the system of record
    checks: list[dict] = []                   # verifier: postconditions
    max_steps: int = 40
    max_tokens: int = 800_000

    @property
    def goal_text(self) -> str:
        return self.goal.format(**self.params)

    @classmethod
    def load(cls, path: str | Path, overrides: dict | None = None) -> "TaskSpec":
        spec = cls(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        spec.params.update(overrides or {})
        return spec


class RunState(BaseModel):
    run_id: str
    task_id: str
    goal: str
    status: Literal["running", "done", "blocked", "failed"] = "running"
    outcome: str = ""                         # final summary or failure reason
    plan: list[dict] = []                     # [{"step": str, "status": "todo|doing|done|skipped"}]
    facts: dict[str, dict] = {}               # key -> {"value", "source", "step"}  (provenance kept)
    messages: list[dict] = []
    step: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    verify_failures: int = 0
    error_streak: int = 0
    call_log: list[str] = Field(default_factory=list)  # hashes of (tool, args) for loop detection

    def save(self, run_dir: Path):
        atomic_write(run_dir / "state.json", self.model_dump_json(indent=1))  # a crash never leaves half a file

    @classmethod
    def load(cls, run_dir: Path) -> "RunState":
        return cls.model_validate_json((run_dir / "state.json").read_text(encoding="utf-8"))

    def working_memory(self) -> str:
        """Recited after every observation (Manus-style) so plan + facts stay in recent attention."""
        plan = " | ".join(f"[{p['status']}] {p['step']}" for p in self.plan) or "(no plan yet -- call update_plan)"
        facts = "; ".join(f"{k}={v['value']}" for k, v in self.facts.items()) or "(none)"
        return f"[working memory] step {self.step} | plan: {plan} | facts: {facts}"
