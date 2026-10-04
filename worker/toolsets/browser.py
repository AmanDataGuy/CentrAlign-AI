"""Browser tools over Playwright. Perception = accessibility snapshot (KB of text with [ref=eN] handles).

Refs change on every navigation, so every action re-snapshots and returns the new page state.
Policy gets a grounded description of the real element behind a ref (+ values typed into the form),
so "click e23" is judged as 'button "Update bank details"', not by what the model says it is.
"""
import os
import re
from typing import Annotated
from urllib.parse import urljoin, urlparse

from pydantic import Field

from ..tools import ToolError, tool

MAX_SNAPSHOT = 12_000
ALLOWED_PATH = re.compile(r"^/(portal|erp)(/|$)")   # the company apps only


def reachable(url: str, origin: str) -> bool:
    """The agent's browser may load the company apps and nothing else: not the operator console, its API,
    admin routes or docs, even when they share the same host and port."""
    return url.startswith(origin + "/") and bool(ALLOWED_PATH.match(urlparse(url).path))
Ref = Annotated[str, Field(description="Element ref from the latest snapshot, e.g. 'e12' or 'f2e15'")]


class Session:
    def __init__(self, headed: bool, origin: str):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        # --disable-dev-shm-usage: containers ship a tiny /dev/shm that crashes Chromium
        self.browser = self.pw.chromium.launch(headless=not headed, slow_mo=250 if headed else 0,
                                               args=["--disable-dev-shm-usage"])
        self.page = self.browser.new_page()
        self.page.set_default_timeout(8000)
        # network allowlist for EVERY request (clicks, redirects, images, beacons), not just browser_open
        self.page.context.route("**/*", lambda r: r.continue_() if reachable(r.request.url, origin) else r.abort())
        self.snapshot, self.url, self.fills = "", "", {}

    def close(self):
        self.browser.close()
        self.pw.stop()


def session(ctx) -> Session:
    if ctx.browser is None:
        ctx.browser = Session(os.environ.get("WORKER_HEADED") == "1", ctx.sandbox_url.rstrip("/"))
    return ctx.browser


def observe(ctx, note: str = "") -> str:
    s = session(ctx)
    try:
        s.page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass  # snapshot below still shows whatever rendered
    if s.page.url != s.url:
        s.url, s.fills = s.page.url, {}
    s.snapshot = s.page.aria_snapshot(mode="ai")
    snap = s.snapshot if len(s.snapshot) <= MAX_SNAPSHOT else s.snapshot[:MAX_SNAPSHOT] + "\n...[truncated]"
    return f"{note}\nURL: {s.page.url}\nTitle: {s.page.title()}\n{snap}".strip()


def _line(s: Session, ref: str) -> str:
    """Label of a ref, for messages only. Anchored to the ref at END of line so page text can't spoof it."""
    m = re.search(rf"^\s*- (.*?) \[ref={re.escape(ref)}\](?: \[[^\]]+\])*:?$", s.snapshot, re.M)
    if not m:
        raise ToolError("state", f"ref {ref} is not on the current page (page changed?). Use a ref from the latest snapshot.")
    return m.group(1)


def _loc(ctx, ref):
    s = session(ctx)
    _line(s, ref)
    return s.page.locator(f"aria-ref={ref}")


# What policy sees comes from the LIVE DOM element, never from page text or the model's claim.
GROUND_JS = """e => { const f = e.closest('form'), row = e.closest('tr'), h = document.querySelector('h1');
  const submits = f && (e.type === 'submit' || (e.tagName === 'BUTTON' && !e.getAttribute('type')));
  return { text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 80),
           tag: e.tagName.toLowerCase(), href: e.getAttribute('href') || '',
           method: submits ? (f.getAttribute('method') || 'get').toLowerCase() : '',
           action: submits ? new URL(f.action).pathname : '',
           context: [(h && h.innerText) || '', row ? row.innerText.replace(/\\s+/g, ' ') : ''].join(' | ').slice(0, 200) }; }"""


def describe_click(ctx, args):
    s = session(ctx)
    g = _loc(ctx, args["ref"]).evaluate(GROUND_JS)
    desc = f'{g["tag"]} "{g["text"]}"' + (f' -> {g["href"]}' if g["href"] else "")
    write = g["action"] if g["method"] == "post" else ""
    if write:
        desc += f" submits POST {write}"
    return {"desc": desc, "write": write, "values": list(s.fills.values()),
            "context": f'page: {g["context"]}' + (f" | form values: {s.fills}" if s.fills else "")}


def describe_fill(ctx, args):
    return {"desc": _line(session(ctx), args["ref"]), "values": [args.get("text") or args.get("option", "")]}


@tool(untrusted=True)
def browser_open(ctx, url: Annotated[str, Field(description="Absolute URL or path like /erp/bills")]):
    """Navigate to a URL inside the company sandbox and return the page snapshot."""
    full = urljoin(ctx.sandbox_url + "/", url)
    if not reachable(full, ctx.sandbox_url.rstrip("/")):
        raise ToolError("bad_args", f"Only the company apps are reachable: paths under /portal and /erp on {ctx.sandbox_url}.")
    resp = session(ctx).page.goto(full)
    if resp and resp.status >= 500:
        raise ToolError("transient", f"HTTP {resp.status} from {full}")
    return observe(ctx)


@tool(untrusted=True)
def browser_snapshot(ctx):
    """Re-read the current page (accessibility tree with element refs)."""
    return observe(ctx)


@tool(risk="low", writes=True, untrusted=True, describe=describe_click)
def browser_click(ctx, ref: Ref):
    """Click an element (link, button). Submitting a form is a write. Returns the resulting page."""
    _loc(ctx, ref).click()
    return observe(ctx, "Clicked.")


@tool(untrusted=True, describe=describe_fill)
def browser_fill(ctx, ref: Ref, text: Annotated[str, Field(description="Text to type (replaces current value)")]):
    """Type into a textbox."""
    s = session(ctx)
    _loc(ctx, ref).fill(text)
    s.fills[_line(s, ref)] = text
    return f"Filled {_line(s, ref)} with {text!r}."  # no re-snapshot: refs stay valid within the page


@tool(untrusted=True, describe=describe_fill)
def browser_select(ctx, ref: Ref, option: Annotated[str, Field(description="Visible option label")]):
    """Choose an option in a dropdown (combobox)."""
    s = session(ctx)
    _loc(ctx, ref).select_option(label=option)
    s.fills[_line(s, ref)] = option
    return f"Selected {option!r} in {_line(s, ref)}."


@tool(untrusted=True, writes=True, describe=describe_click)
def browser_download(ctx, ref: Ref):
    """Click a download link and save the file into the workspace inbox. Returns the saved path."""
    s = session(ctx)
    with s.page.expect_download() as dl:
        _loc(ctx, ref).click()
    inbox = ctx.workdir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / os.path.basename(dl.value.suggested_filename)  # server-controlled name: strip any path
    dl.value.save_as(path)
    return f"Saved {path.relative_to(ctx.workdir).as_posix()} ({path.stat().st_size} bytes). Read it with files_read."


def screenshot(ctx, name: str) -> str | None:
    """Evidence capture (not a tool): used by finish and on failures."""
    if ctx.browser is None:
        return None
    path = ctx.run_dir / f"{name}.png"
    ctx.browser.page.screenshot(path=path, full_page=True)
    return str(path)
