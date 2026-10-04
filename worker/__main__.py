"""CLI.
  python -m worker run tasks/invoice_to_erp.yaml [-p vendor="Globex Corp"] [--approve ask|yes|no]
                       [--no-verify] [--headed] [--resume RUN_ID] [--goal "free text overrides the spec goal"]
  python -m worker tasks
"""
import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from . import llm as llm_mod
from .loop import run_task
from .models import TaskSpec
from .policy import cli_approver, fixed_approver


def load_dotenv(path=Path(".env")):
    # ponytail: 5-line .env reader instead of python-dotenv
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser(prog="worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("task")
    r.add_argument("-p", "--param", action="append", default=[], help="key=value")
    r.add_argument("--goal", help="Natural-language goal overriding the spec's goal template")
    r.add_argument("--approve", choices=["ask", "yes", "no"], default="ask")
    r.add_argument("--no-verify", action="store_true", help="Ablation: trust the agent's own claim")
    r.add_argument("--headed", action="store_true")
    r.add_argument("--resume")
    sub.add_parser("tasks")
    a = ap.parse_args(argv)
    console = Console()

    if a.cmd == "tasks":
        for f in sorted(Path("tasks").glob("*.yaml")):
            s = TaskSpec.load(f)
            console.print(f"[bold]{f}[/]  {s.goal_text}")
        return 0

    if a.headed:
        os.environ["WORKER_HEADED"] = "1"
    spec = TaskSpec.load(a.task, dict(p.split("=", 1) for p in a.param))
    if a.goal:
        spec.goal = a.goal.replace("{", "{{").replace("}", "}}")
    approver = cli_approver(console) if a.approve == "ask" else fixed_approver(a.approve == "yes")
    ask_human = (lambda q: console.input(f"[bold cyan]Agent asks:[/] {escape(q)}\n> ")) if sys.stdin.isatty() else \
        (lambda q: "No human is available. Proceed only if it is safe; otherwise finish with status 'blocked'.")

    console.print(Panel(spec.goal_text, title=f"Task {spec.id}", border_style="cyan"))
    state, verdict, run_dir = run_task(spec, llm_mod.from_env(), approver, ask_human,
                                       sandbox_url=os.environ.get("SANDBOX_URL", "http://127.0.0.1:8001"),
                                       verify_enabled=not a.no_verify, console=console, resume_id=a.resume)
    unverifiable = a.no_verify or not spec.checks  # None is only acceptable when nothing was meant to be checked
    ok = state.status == "done" and (verdict.ok if verdict else unverifiable)
    console.print(Panel(f"[bold]{state.status.upper()}[/] -- {escape(state.outcome)}\n\n"
                        f"Independent verification:\n"
                        f"{escape(verdict.summary()) if verdict else 'not configured' if unverifiable else 'ERRORED (see trace)'}\n\n"
                        f"Report: {run_dir / 'report.md'}",
                        title="Result", border_style="green" if ok else "red"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
