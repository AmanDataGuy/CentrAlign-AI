"""File tools, jailed to the run workspace."""
from typing import Annotated

from pydantic import Field

from ..tools import ToolError, tool

Path_ = Annotated[str, Field(description="Path relative to the workspace, e.g. 'inbox/INV-1.txt'")]


def _jail(ctx, rel: str):
    root = ctx.workdir.resolve()
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        raise ToolError("bad_args", "Path escapes the workspace.")
    return p


@tool()
def files_list(ctx):
    """List files in the workspace."""
    files = [p.relative_to(ctx.workdir).as_posix() for p in ctx.workdir.rglob("*") if p.is_file()]
    return "\n".join(sorted(files)) or "(workspace is empty)"


@tool(untrusted=True)
def files_read(ctx, path: Path_):
    """Read a text file from the workspace."""
    p = _jail(ctx, path)
    if not p.is_file():
        raise ToolError("bad_args", f"No such file: {path}. Use files_list.")
    return p.read_text(encoding="utf-8", errors="replace")[:20_000]


@tool(risk="low", writes=True)
def files_write(ctx, path: Path_, content: str):
    """Write a text file (reports, extracted data) into the workspace 'out/' folder."""
    p = _jail(ctx, "out/" + path.removeprefix("out/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {p.relative_to(ctx.workdir.resolve()).as_posix()} ({len(content)} chars)."
