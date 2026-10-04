"""Tool registry. A capability = a decorated function; the agent core never changes to add one.

Each tool declares what policy needs to know: risk tier, whether it writes, whether its output is
untrusted external content. Risk is declared HERE, by us -- never taken from the tool/server itself
(MCP annotations are explicitly untrusted hints).
"""
import fnmatch
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, create_model

Risk = Literal["read", "low", "high"]


class ToolError(Exception):
    """kind: transient (retry may help) | bad_args (model must fix) | state (world not as expected) | permanent."""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable
    params: type[BaseModel]
    risk: Risk
    writes: bool
    untrusted: bool
    describe: Callable | None = None  # (ctx, args) -> {"desc": str, "values": [str]} grounded in real UI state

    def schema(self) -> dict:
        s = self.params.model_json_schema()
        defs = s.pop("$defs", {})
        s.pop("title", None)
        return {"name": self.name, "description": self.description, "parameters": _inline(s, defs)}


def _inline(node, defs):
    """Replace {"$ref": "#/$defs/X"} with the definition: some providers reject $ref in tool schemas."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


REGISTRY: dict[str, Tool] = {}


def tool(risk: Risk = "read", writes: bool = False, untrusted: bool = False, describe: Callable | None = None):
    """Register fn(ctx, **args). Params schema comes from the signature (Annotated[..., Field(description=...)])."""
    def deco(fn):
        sig = list(inspect.signature(fn).parameters.values())[1:]  # skip ctx
        fields = {p.name: (p.annotation, ... if p.default is inspect.Parameter.empty else p.default) for p in sig}
        REGISTRY[fn.__name__] = Tool(fn.__name__, inspect.getdoc(fn) or "", fn,
                                     create_model(fn.__name__, **fields), risk, writes, untrusted, describe)
        return fn
    return deco


def select(patterns: list[str]) -> list[Tool]:
    """Tools allowed for a task, by glob (e.g. 'browser_*')."""
    return [t for n, t in REGISTRY.items() if any(fnmatch.fnmatch(n, p) for p in patterns)]


@dataclass
class Ctx:
    """Everything a tool may touch. Passed explicitly -- no globals."""
    spec: Any
    state: Any
    trace: Any
    sandbox_url: str
    workdir: Path
    ask_human: Callable[[str], str]
    run_dir: Path
    verify: Callable[[], Any] | None = None
    last_verdict: Any = None
    browser: Any = None
    evidence: list[str] = field(default_factory=list)
