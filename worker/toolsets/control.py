"""Control tools: the agent manages its own plan/memory, asks humans, and claims completion -- which is then verified."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..tools import tool
from .browser import screenshot

MAX_VERIFY_FAILURES = 3


class PlanStep(BaseModel):
    step: str
    status: Literal["todo", "doing", "done", "skipped"] = "todo"


@tool()
def update_plan(ctx, steps: list[PlanStep]):
    """Set or revise your plan (full list). Call at the start and whenever reality changes the plan."""
    ctx.state.plan = [dict(s) if isinstance(s, dict) else s.model_dump() for s in steps]
    return "Plan updated."


@tool()
def remember(ctx, key: str, value: str,
             source: Annotated[str, Field(description="Where this came from, e.g. 'VendorHub invoice page INV-1'")]):
    """Store a fact you discovered (ids, amounts, dates) with its source. It stays in working memory."""
    ctx.state.facts[key] = {"value": value, "source": source, "step": ctx.state.step}
    return f"Remembered {key}."


@tool()
def ask_user(ctx, question: Annotated[str, Field(description="One specific question; include the options you found")]):
    """Ask the human requester when information is missing or the request is ambiguous in a way that changes the outcome."""
    answer = ctx.ask_human(question)
    ctx.trace.emit("ask_user", question=question, answer=answer)
    return f"User answered: {answer}"


@tool()
def finish(ctx, summary: Annotated[str, Field(description="What was done, with key values (ids, amounts, dates)")],
           status: Literal["done", "blocked"] = "done"):
    """Declare the task complete ('done') or impossible to complete safely ('blocked', explain why).
    'done' triggers independent verification against the system of record."""
    st = ctx.state
    if status == "blocked":
        st.status, st.outcome = "blocked", summary
        return "Recorded as blocked."
    if ctx.verify is None:  # ablation mode: trust the agent's claim
        st.status, st.outcome = "done", summary
        return "Recorded (verification disabled)."
    v = ctx.verify()
    ctx.last_verdict = v
    ctx.trace.emit("verify", ok=v.ok, checks=[{k: c[k] for k in ("name", "ok", "detail")} for c in v.checks])
    if v.ok:
        st.status, st.outcome = "done", summary
        if shot := screenshot(ctx, "final"):
            ctx.evidence.append(shot)
        return "VERIFIED against the system of record:\n" + v.summary()
    st.verify_failures += 1
    if st.verify_failures >= MAX_VERIFY_FAILURES:
        st.status, st.outcome = "failed", f"Verification failed {st.verify_failures}x: {v.summary()}"
    return ("VERIFICATION FAILED -- the system of record does not match the goal:\n" + v.summary() +
            "\nInvestigate (look at the actual records), fix the problem, then call finish again.")
