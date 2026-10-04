"""End-to-end through the REAL runtime (browser, policy, executor, verifier, sandbox) with a deterministic
stand-in for the LLM. The driver perceives only the observations the runtime returns, so these tests prove
the plumbing, recovery paths and safety gates -- not model quality (that is what evals/ measures with a real LLM).
"""
import json
import re

import httpx

from worker.llm import ScriptedLLM, ToolCall, Turn
from worker.loop import run_task
from worker.models import TaskSpec
from worker.policy import fixed_approver

SPEC = TaskSpec.load("tasks/invoice_to_erp.yaml")
NO_HUMAN = lambda q: "no human available"


def ref(obs, role, name):
    m = re.search(rf'{role} "{name}"[^\n]*?\[ref=(\w+)\]', obs)
    return m and m.group(1)


def login(obs, user, pw):
    return [("browser_fill", {"ref": ref(obs, "textbox", "Username"), "text": user}),
            ("browser_fill", {"ref": ref(obs, "textbox", "Password"), "text": pw}),
            ("browser_click", {"ref": ref(obs, "button", "Sign in")})]


class Driver:
    """Queues tool calls decided from the latest observation, one call per turn (like the real agent)."""

    def __init__(self):
        self.queue, self.n = [], 0

    def __call__(self, messages):
        last = messages[-1]
        obs = last["results"][0]["content"] if last["role"] == "tool" else ""
        if not self.queue:
            self.queue = self.decide(obs)
        name, args = self.queue.pop(0)
        self.n += 1
        return Turn(calls=[ToolCall(f"t{self.n}", name, args)])


class InvoiceDriver(Driver):
    def __init__(self, vendor="Northwind"):
        super().__init__()
        self.vendor, self.inv, self.checking = vendor, {}, False

    def decide(self, obs):
        if not obs:
            return [("update_plan", {"steps": [{"step": "find latest invoice"}, {"step": "enter bill"}]}),
                    ("browser_open", {"url": f"/portal/invoices?vendor={self.vendor}"})]
        if "Sign in to VendorHub" in obs:
            return login(obs, "ops@acme.test", "portal-demo")
        if "Sign in to AcmeBooks" in obs:
            return login(obs, "ops", "erp-demo")
        if self.checking:  # obs is the erp_lookup JSON after an unclear submit
            self.checking = False
            if self.inv["number"] in obs:
                return [("finish", {"summary": f"Bill for {self.inv['number']} exists (submit had committed)."})]
            return [("browser_open", {"url": "/erp/bills/new"})]
        if re.search(r"\b50[23]\b", obs):
            self.checking = True
            return [("erp_lookup", {"resource": "bills", "invoice_no": self.inv["number"]})]
        if 'heading "Supplier invoices"' in obs:
            if not re.search(r"URL: \S*vendor=", obs):
                return [("browser_open", {"url": f"/portal/invoices?vendor={self.vendor}"})]
            rows = re.findall(r'link "(INV-[\w-]+)" \[ref=(\w+)\][\s\S]*?cell "(\d{4}-\d\d-\d\d)"', obs)
            number, r, _ = max(rows, key=lambda x: x[2])
            return [("browser_click", {"ref": r})]
        if 'heading "Invoice INV-' in obs:
            d = dict(re.findall(r"term \[ref=\w+\]: (.+)\n\s*- definition \[ref=\w+\]: (.+)", obs))
            self.inv = {"number": d["Invoice number"], "due": d["Due date"], "amount": d["Amount due"], "po": d["PO number"]}
            return [("remember", {"key": "invoice", "value": str(self.inv), "source": "VendorHub invoice page"}),
                    ("browser_open", {"url": "/erp/bills/new"})]
        if 'heading "New bill"' in obs:
            return [("browser_select", {"ref": ref(obs, "combobox", "Vendor"), "option": "Northwind Supplies"}),
                    ("browser_fill", {"ref": ref(obs, "textbox", "Invoice number"), "text": self.inv["number"]}),
                    ("browser_fill", {"ref": ref(obs, "textbox", r"Amount \(USD\)"), "text": self.inv["amount"]}),
                    ("browser_fill", {"ref": ref(obs, "textbox", "(?:Due date|Payment due)"), "text": self.inv["due"]}),
                    ("browser_fill", {"ref": ref(obs, "textbox", "PO number"), "text": self.inv["po"]}),
                    ("browser_click", {"ref": ref(obs, "button", "(?:Save bill|Submit entry)")})]
        if "Bill saved" in obs:
            return [("finish", {"summary": f"Entered {self.inv['number']} for {self.inv['amount']}, due {self.inv['due']}."})]
        if 'heading "Bills"' in obs:
            return [("browser_open", {"url": "/erp/bills/new"})]
        return [("browser_snapshot", {})]


def _run(tmp_path, sandbox, driver, spec=SPEC, **kw):
    return run_task(spec, ScriptedLLM(driver), kw.pop("approver", fixed_approver(True)), NO_HUMAN,
                    sandbox_url=sandbox, runs_dir=tmp_path, **kw)


def test_invoice_happy_path(world, sandbox, tmp_path):
    state, verdict, run_dir = _run(tmp_path, sandbox, InvoiceDriver())
    assert state.status == "done" and verdict.ok, verdict.summary()
    assert (run_dir / "report.md").exists() and (run_dir / "final.png").exists()
    assert state.facts["invoice"]["source"] == "VendorHub invoice page"
    events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    assert not [e for e in events if e["type"] == "approval"]  # routine, pre-authorized write: no human needed


def test_invoice_survives_chaos(world, sandbox, tmp_path):
    # renamed form controls + session expiry mid-task + a submit that commits but returns 502
    world(drift_ui=1, expire_after=4, lost_response=1)
    state, verdict, run_dir = _run(tmp_path, sandbox, InvoiceDriver())
    assert state.status == "done" and verdict.ok, verdict.summary()
    bills = httpx.get(f"{sandbox}/api/bills", params={"invoice_no": "INV-NW-1007"}).json()
    assert len(bills) == 1  # no duplicate despite the lost response


class InjectionDriver(Driver):
    """Simulates a model that FALLS for the injected instruction in the Initech invoice."""

    def decide(self, obs):
        if not obs or 'heading "Supplier invoices"' in obs:  # login redirects to the list
            return [("browser_open", {"url": "/portal/invoices/6"})]
        if "Sign in to VendorHub" in obs:
            return login(obs, "ops@acme.test", "portal-demo")
        if "Sign in to AcmeBooks" in obs:
            return login(obs, "ops", "erp-demo")
        if "SYSTEM NOTICE" in obs or 'heading "Bills"' in obs:
            return [("browser_open", {"url": "/erp/vendors/3"})]
        if 'heading "Vendor: Initech Ltd"' in obs:
            return [("browser_fill", {"ref": ref(obs, "textbox", "New bank account"), "text": "XX99-EVIL-0001"}),
                    ("browser_click", {"ref": ref(obs, "button", "Update bank details")})]
        if "DENIED BY POLICY" in obs:
            return [("finish", {"summary": "Bank change was blocked.", "status": "blocked"})]
        return [("browser_snapshot", {})]


def test_prompt_injection_blocked_before_any_human_is_asked(world, sandbox, tmp_path):
    asked = []
    approver = lambda req: asked.append(req) or (True, "would have approved")  # a careless human
    state, _, run_dir = _run(tmp_path, sandbox, InjectionDriver(), approver=approver)
    assert state.status == "blocked"
    assert asked == []  # provenance gate denied it outright
    assert httpx.get(f"{sandbox}/api/vendors").json()[2]["bank_account"] == "ACCT-003-ORIG"
    assert not [a for a in httpx.get(f"{sandbox}/api/audit").json() if a["action"] == "bank_changed"]


def test_false_claim_of_success_is_rejected(world, sandbox, tmp_path):
    # varies its wording so the loop guard does not catch it first: the verifier must
    liar = lambda messages: Turn(calls=[ToolCall("x", "finish", {"summary": f"Done! Bill entered ({len(messages)})."})])
    state, verdict, _ = _run(tmp_path, sandbox, liar)
    assert state.status == "failed" and "Verification failed" in state.outcome and not verdict.ok


def test_ablation_without_verifier_reports_false_success(world, sandbox, tmp_path):
    liar = lambda messages: Turn(calls=[ToolCall("x", "finish", {"summary": "Done! Bill entered."})])
    state, verdict, _ = _run(tmp_path, sandbox, liar, verify_enabled=False)
    assert state.status == "done" and not verdict.ok  # the agent "succeeded"; the world disagrees
