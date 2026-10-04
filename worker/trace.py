"""Append-only run trace (JSONL, OTel-ish field names) + live console view + final markdown report."""
import json
from datetime import datetime, timezone
from pathlib import Path

from rich.markup import escape  # model/page text may contain "[...]" -- never interpret it as markup

STYLE = {"llm": "cyan", "tool": "green", "tool_error": "red", "policy": "magenta", "approval": "yellow",
         "verify": "bold blue", "loop_guard": "red", "run": "bold"}


class Trace:
    def __init__(self, run_dir: Path, console=None):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path, self.console, self.step = run_dir / "trace.jsonl", console, 0

    def emit(self, type_: str, **kw):
        ev = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": self.step, "type": type_, **kw}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        if self.console and not (type_ == "policy" and ev.get("verdict") == "allow"):
            body = {k: v for k, v in kw.items() if v not in ("", None, [])}
            self.console.print(f"[{STYLE.get(type_, 'white')}]{self.step:>3} {type_:<10}[/] "
                               f"{escape(json.dumps(body, default=str)[:400])}", highlight=False)

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]


def write_report(run_dir: Path, state, verdict, evidence: list[str]) -> Path:
    events = Trace(run_dir).events()
    count = lambda t: sum(e["type"] == t for e in events)
    lines = [f"# Run {state.run_id} -- {state.task_id}", "", f"**Goal:** {state.goal}", "",
             f"**Status:** `{state.status}`  ", f"**Outcome:** {state.outcome}", "",
             f"Steps: {state.step} | tool calls: {count('tool')} | errors: {count('tool_error')} | "
             f"approvals asked: {count('approval')} | tokens in/out: {state.tokens_in}/{state.tokens_out} | "
             f"cost: ${state.cost_usd:.4f}", "",
             "## Independent verification (system of record)", "```", verdict.summary() if verdict else "not run", "```", "",
             "## Facts remembered (with provenance)", *[f"- **{k}** = {v['value']}  _(source: {v['source']})_"
                                                       for k, v in state.facts.items()], "",
             "## Timeline", "| step | event | detail |", "|---|---|---|"]
    for e in events:
        if e["type"] in ("tool", "tool_error", "approval", "verify", "loop_guard") or \
                (e["type"] == "policy" and e["verdict"] != "allow"):
            detail = {k: v for k, v in e.items() if k not in ("ts", "step", "type", "result")}
            lines.append(f"| {e['step']} | {e['type']} | {json.dumps(detail, default=str)[:220].replace('|', '/')} |")
    lines += ["", "## Evidence", *[f"![{Path(p).name}]({Path(p).name})" for p in evidence]]
    out = run_dir / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
