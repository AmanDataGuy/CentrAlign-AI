"""Runs one proposed tool call: loop-guard -> validate -> policy -> approval -> invoke (retry transient reads) -> observe."""
import hashlib
import json
import time

from pydantic import ValidationError

from . import policy
from .tools import REGISTRY, ToolError

HINTS = {
    "transient": "Temporary failure. If this was a submit/write, CHECK whether it already took effect before repeating it.",
    "state": "The page/system is not in the state you assumed. Re-observe (browser_snapshot) and pick a fresh element.",
    "bad_args": "Fix the arguments and try again.",
    "permanent": "This will not succeed by retrying. Choose a different approach or ask_user.",
}
MAX_ATTEMPTS = 3


class Executor:
    def __init__(self, ctx, approver, run_dir):
        self.ctx, self.approver = ctx, approver
        self.corpus_file = run_dir / "untrusted.txt"  # full untrusted text survives context compaction

    def _corpus(self) -> str:
        return self.corpus_file.read_text(encoding="utf-8") if self.corpus_file.exists() else ""

    def run(self, call: dict) -> dict:
        state, trace, name, args = self.ctx.state, self.ctx.trace, call["name"], call["args"]
        res = lambda content, err=False, untrusted=False: {"id": call["id"], "content": content, "is_error": err,
                                                            "untrusted": untrusted}

        # 1. loop guard: identical (tool, args) three times in a row means no progress
        h = hashlib.sha1(f"{name}{json.dumps(args, sort_keys=True)}".encode()).hexdigest()[:12]
        state.call_log.append(h)
        if state.call_log[-3:] == [h] * 3:
            state.error_streak += 1
            trace.emit("loop_guard", tool=name)
            return res("LOOP DETECTED: you made this exact call 3 times in a row with no progress. "
                       "Change approach (different element, re-login, check state) or ask_user.", True)

        # 2. schema validation at the boundary
        tool = REGISTRY.get(name)
        if "_invalid_json" in args:
            state.error_streak += 1
            return res(f"BAD ARGUMENTS: not valid JSON: {str(args['_invalid_json'])[:200]}", True)
        try:
            parsed = tool.params.model_validate(args).model_dump() if tool else args
        except ValidationError as e:
            state.error_streak += 1
            return res(f"BAD ARGUMENTS: {e.errors(include_url=False)}", True)

        # 3. deterministic policy, grounded in real UI state (not the model's description of it)
        grounded = {}
        if tool and tool.describe:
            try:
                grounded = tool.describe(self.ctx, parsed) or {}
            except Exception as e:  # fail closed: policy denies writes it cannot ground
                grounded = {"desc": "(unresolved target)", "error": str(e)[:200]}
        d = policy.decide(tool, parsed, self.ctx.spec, state, grounded, self._corpus())
        trace.emit("policy", tool=name, verdict=d.verdict, reason=d.reason, target=grounded.get("desc", ""))
        if d.verdict == "deny":
            state.error_streak += 1
            return res(f"DENIED BY POLICY: {d.reason}", True)
        if d.verdict == "ask":
            ok, note = self.approver({"tool": name, "args": parsed, "reason": d.reason,
                                      "desc": " | ".join(filter(None, [grounded.get("desc"), grounded.get("context")]))})
            trace.emit("approval", tool=name, approved=ok, note=note)
            if not ok:
                return res(f"HUMAN DENIED this action ({note}). Do not attempt it again or work around it. "
                           "Continue with what is permitted, or finish with status 'blocked' and explain.", True)

        # 4. invoke; only side-effect-free tools are retried automatically
        for attempt in range(1, MAX_ATTEMPTS + 1):
            t0 = time.monotonic()
            try:
                out = tool.fn(self.ctx, **parsed)
                break
            except ToolError as e:
                kind, msg = e.kind, str(e)
            except Exception as e:  # unknown failure: surface it, never swallow
                kind, msg = ("transient" if "Timeout" in type(e).__name__ else "permanent"), f"{type(e).__name__}: {e}"
            trace.emit("tool_error", tool=name, kind=kind, error=msg[:500], attempt=attempt)
            if kind == "transient" and not tool.writes and attempt < MAX_ATTEMPTS:
                time.sleep(0.5 * 3 ** (attempt - 1))
                continue
            state.error_streak += 1
            # error text from a page/file tool can carry page content too -> same untrusted handling
            return res(self._mark(tool, f"{kind.upper()} ERROR: {msg}") + f"\nHint: {HINTS[kind]}", True, tool.untrusted)

        state.error_streak = 0
        out = str(out)
        trace.emit("tool", tool=name, args=parsed, ms=int((time.monotonic() - t0) * 1000), result=out[:300])
        return res(self._mark(tool, out), untrusted=tool.untrusted)

    def _mark(self, tool, text: str) -> str:
        """Untrusted output: kept in full on disk for the provenance gate, and fenced for the model."""
        if not tool.untrusted:
            return text
        with self.corpus_file.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
        return "<untrusted_content>\n" + text + "\n</untrusted_content>"
