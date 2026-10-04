"""Deterministic authorization. The LLM proposes a tool call; this code disposes. No model in this path.

Order: unknown tool -> not allowed for task -> budget -> risk rules / high-risk tier
       -> provenance gate (prompt-injection) -> ask a human.
"""
import fnmatch
import json
import re
from dataclasses import dataclass

from rich.markup import escape

from .models import RiskRule

# Applied to every task. Matched against the tool args AND the grounded description of what the
# action really does (e.g. the accessible name of the button behind a ref), never the model's claim.
DEFAULT_RULES = [
    RiskRule(tool="browser_*", match=r"(?i)\b(update bank|bank details|delete|remove|transfer|wire|pay now|approve)\b",
             reason="Consequential action: money movement, approval or destructive change"),
]
ALWAYS_WRITABLE = ["/*/login"]  # signing in is not a business write
MIN_INJECT_LEN = 6  # normalized; shorter strings ("yes", "1") collide with everything


@dataclass
class Decision:
    verdict: str  # allow | ask | deny
    reason: str = ""


def decide(tool, args: dict, spec, state, grounded: dict, untrusted_corpus: str) -> Decision:
    if tool is None:
        return Decision("deny", "Unknown tool.")
    if not any(fnmatch.fnmatch(tool.name, p) for p in spec.allowed_tools):
        return Decision("deny", f"{tool.name} is not permitted for task '{spec.id}'.")
    if state.step > spec.max_steps or state.tokens_in + state.tokens_out >= spec.max_tokens:
        return Decision("deny", "Step/token budget exhausted.")

    if tool.writes and grounded.get("error"):
        return Decision("deny", f"Could not resolve what this action targets ({grounded['error']}); refusing to guess.")

    blob = json.dumps(args) + " " + grounded.get("desc", "")
    hit = next((r for r in spec.risk_rules + DEFAULT_RULES
                if fnmatch.fnmatch(tool.name, r.tool) and re.search(r.match, blob)), None)
    # A form POST is a write no matter what its button says: judge it by WHERE it submits.
    write = grounded.get("write", "")
    unlisted_write = write and not any(fnmatch.fnmatch(write, p) for p in [*spec.allowed_writes, *ALWAYS_WRITABLE])
    if not hit and not unlisted_write and tool.risk != "high":
        return Decision("allow")

    # Provenance gate: a consequential action whose values exist only in untrusted external content
    # (web pages, files) and nowhere in the user's request or company context is treated as injection.
    trusted, corpus = _norm(spec.goal_text + "\n" + spec.context), _norm(untrusted_corpus)
    values = [v for v in grounded.get("values", []) + [str(a) for a in args.values()] if len(_norm(v)) >= MIN_INJECT_LEN]
    injected = [v for v in values if _norm(v) in corpus and _norm(v) not in trusted]
    if injected:
        return Decision("deny", f"Blocked: {injected} come only from untrusted external content, not from the user "
                                f"or company context (possible prompt injection). Do not retry; report it.")
    if hit and hit.action == "deny":
        return Decision("deny", hit.reason)
    if hit:
        return Decision("ask", hit.reason)
    if unlisted_write:
        return Decision("ask", f"Submits a write to {write}, which this task does not pre-authorize.")
    return Decision("ask", f"{tool.name} is a high-risk tool.")


def _norm(s: str) -> str:
    """Compare values the way a fraudster would vary them: ignore case, spaces and punctuation."""
    return re.sub(r"[\W_]+", "", s).lower()


# ---------- approvers: who answers an "ask" ----------
def fixed_approver(answer: bool):
    """Non-interactive (evals/CI). The decision is logged in the trace like any human answer."""
    return lambda req: (answer, "auto-approved" if answer else "auto-denied")


def cli_approver(console):
    def ask(req: dict):
        console.rule("[bold yellow]APPROVAL REQUIRED")
        console.print(f"[bold]{req['tool']}[/] {escape(json.dumps(req['args']))}\n{escape(req.get('desc', ''))}\n"
                      f"[yellow]Why:[/] {escape(req['reason'])}")
        yes = console.input("Approve? [y/N] ").strip().lower() == "y"
        note = "" if yes else console.input("Reason / instruction for the agent (optional): ").strip()
        return yes, note or ("approved by human" if yes else "denied by human")
    return ask
