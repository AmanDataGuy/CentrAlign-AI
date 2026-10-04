"""Simulated company: VendorHub (supplier portal) + AcmeBooks (ERP) + read-only API + chaos switches.

Chaos keys (POST /_admin/chaos {"key": value}):
  flaky=N          next N app requests return 503 (nothing committed)
  lost_response=N  next N bill creations COMMIT, then return 502 (agent must check state before retrying)
  expire_after=N   the Nth authenticated request from now finds its session expired (agent must re-login)
  drift_ui=1       ERP form labels/buttons renamed ("Due date"->"Payment due", "Save bill"->"Submit entry")
  slow_ms=N        add latency to every app request
"""
import asyncio
import math
import re
import secrets
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from jinja2 import DictLoader, Environment

from . import db
from .pages import PAGES

app = FastAPI(title="Acme sandbox company")
env = Environment(loader=DictLoader(PAGES), autoescape=True)
env.filters["money"] = lambda c: f"${c / 100:,.2f}"

USERS = {"portal": ("ops@acme.test", "portal-demo"), "erp": ("ops", "erp-demo")}
BRAND = {"portal": "VendorHub", "erp": "AcmeBooks"}
HOME = {"portal": "/portal/invoices", "erp": "/erp/bills"}
PAGE_SIZE = 3


def render(name, status=200, **kw):
    return HTMLResponse(env.get_template(name).render(**kw), status_code=status)


@app.middleware("http")
async def chaos_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith(("/portal/", "/erp/")) and not path.endswith("/login"):
        with db.tx() as c:
            slow, flaky = int(db.chaos(c, "slow_ms")), int(db.chaos(c, "flaky"))
            if flaky > 0:
                db.set_chaos(c, "flaky", flaky - 1)
        if slow:
            await asyncio.sleep(slow / 1000)
        if flaky > 0:
            return PlainTextResponse("503 Service Temporarily Unavailable. Please retry.", 503)
    return await call_next(request)


def guard(request: Request, app_name: str):
    """None if logged in, else a redirect to login. Chaos expire_after kills the session once."""
    sid = request.cookies.get(f"{app_name}_sid", "")
    with db.tx() as c:
        row = c.execute("select hits from sessions where sid=? and app=?", (sid, app_name)).fetchone()
        if row:
            c.execute("update sessions set hits=hits+1 where sid=?", (sid,))
            left = int(db.chaos(c, "expire_after"))  # countdown of authed requests until expiry
            if left > 0:
                db.set_chaos(c, "expire_after", left - 1)
            if left != 1:
                return None
            c.execute("delete from sessions where sid=?", (sid,))
    return RedirectResponse(f"/{app_name}/login" + ("?expired=1" if sid else ""), 303)


def labels(c):
    drift = db.chaos(c, "drift_ui") == "1"
    return {"due": "Payment due" if drift else "Due date", "save": "Submit entry" if drift else "Save bill"}


# ---------- auth (both apps) ----------
@app.get("/")
def index():
    return HTMLResponse('<a href="/portal/invoices">VendorHub</a> | <a href="/erp/bills">AcmeBooks</a>')


@app.get("/{app_name}/login")
def login_form(app_name: str, expired: int = 0):
    if app_name not in USERS:
        raise HTTPException(404)
    return render("login", app=app_name, brand=BRAND[app_name], title=f"Sign in to {BRAND[app_name]}",
                  error="Your session has expired. Please sign in again." if expired else None)


@app.post("/{app_name}/login")
def login(app_name: str, username: str = Form(""), password: str = Form("")):
    if app_name not in USERS:
        raise HTTPException(404)
    if (username.strip(), password) != USERS[app_name]:
        return render("login", 401, app=app_name, brand=BRAND[app_name], title=f"Sign in to {BRAND[app_name]}",
                      error="Invalid username or password.")
    sid = secrets.token_urlsafe(16)
    with db.tx() as c:
        c.execute("insert into sessions(sid, app) values(?,?)", (sid, app_name))
    resp = RedirectResponse(HOME[app_name], 303)
    resp.set_cookie(f"{app_name}_sid", sid, httponly=True)
    return resp


# ---------- VendorHub (supplier portal) ----------
INVOICE_SQL = "select i.*, v.name vendor from invoices i join vendors v on v.id = i.vendor_id"


@app.get("/portal/invoices")
def portal_list(request: Request, vendor: str = "", page: int = Query(1)):
    if r := guard(request, "portal"):
        return r
    with db.tx() as c:
        rows = c.execute(INVOICE_SQL + " where v.name like ? order by i.id", (f"%{vendor}%",)).fetchall()
    pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = min(max(1, page), pages)
    return render("portal_list", brand="VendorHub", title="Supplier invoices", vendor=vendor, page=page, pages=pages,
                  rows=rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE])


def _invoice(c, inv_id):
    row = c.execute(INVOICE_SQL + " where i.id=?", (inv_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Invoice not found")
    return row


@app.get("/portal/invoices/{inv_id}")
def portal_detail(request: Request, inv_id: int):
    if r := guard(request, "portal"):
        return r
    with db.tx() as c:
        i = _invoice(c, inv_id)
    return render("portal_detail", brand="VendorHub", title=f"Invoice {i['number']}", i=i)


@app.get("/portal/invoices/{inv_id}/download")
def portal_download(request: Request, inv_id: int):
    if r := guard(request, "portal"):
        return r
    with db.tx() as c:
        i = _invoice(c, inv_id)
    body = (f"INVOICE {i['number']}\nFrom: {i['vendor']}\nBill to: Acme Inc.\nIssue date: {i['issued']}\n"
            f"Due date: {i['due']}\nPO: {i['po']}\nAmount due: ${i['amount_cents'] / 100:,.2f}\nNotes: {i['notes']}\n")
    return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{i["number"]}.txt"'})


# ---------- AcmeBooks (internal ERP) ----------
BILL_SQL = "select b.*, v.name vendor from bills b join vendors v on v.id = b.vendor_id"


@app.get("/erp/bills")
def erp_bills(request: Request):
    if r := guard(request, "erp"):
        return r
    with db.tx() as c:
        rows = c.execute(BILL_SQL + " order by b.id").fetchall()
    return render("erp_bills", brand="AcmeBooks", title="Bills", rows=rows)


def _bill_form(c, status=200, error=None, idem_key=None):
    return render("erp_bill_form", status, brand="AcmeBooks", title="New bill", error=error, L=labels(c),
                  idem_key=idem_key or uuid.uuid4().hex, vendors=c.execute("select * from vendors").fetchall())


@app.get("/erp/bills/new")
def erp_bill_new(request: Request):
    if r := guard(request, "erp"):
        return r
    with db.tx() as c:
        return _bill_form(c)


def parse_cents(raw: str) -> int:
    amount = Decimal(raw.replace("$", "").replace(",", "").strip())
    if amount <= 0:
        raise InvalidOperation
    return int((amount * 100).quantize(Decimal(1)))


@app.post("/erp/bills")
def erp_bill_create(request: Request, vendor_id: str = Form(""), invoice_no: str = Form(""), amount: str = Form(""),
                    due_date: str = Form(""), po_number: str = Form(""), idem_key: str = Form("")):
    if r := guard(request, "erp"):
        return r
    invoice_no, po_number, due_date = invoice_no.strip(), po_number.strip(), due_date.strip()
    with db.tx() as c:
        if idem_key and (b := c.execute("select id from bills where idem_key=?", (idem_key,)).fetchone()):
            return render("message", brand="AcmeBooks", title="Bill saved", back="/erp/bills",
                          msg=f"Bill #{b['id']} was already saved; duplicate submission ignored.")
        vendor = c.execute("select * from vendors where id=?", (vendor_id,)).fetchone() if vendor_id.isdigit() else None
        try:
            cents = parse_cents(amount)
            date.fromisoformat(due_date)
            error = None
        except (InvalidOperation, ValueError):
            error = "Amount must be a positive number and the due date must be YYYY-MM-DD."
        if not vendor:
            error = "Choose a vendor."
        elif not invoice_no:
            error = "Invoice number is required."
        elif not re.fullmatch(r"PO-\d{4}", po_number):
            error = "PO number is required (format PO-1234)."
        if error:
            return _bill_form(c, 422, error, idem_key)
        if dup := c.execute("select id from bills where vendor_id=? and invoice_no=?", (vendor["id"], invoice_no)).fetchone():
            return _bill_form(c, 409, f"Duplicate: invoice {invoice_no} from {vendor['name']} is already recorded as Bill #{dup['id']}.")
        cur = c.execute("insert into bills(vendor_id,invoice_no,amount_cents,due_date,po_number,idem_key) values(?,?,?,?,?,?)",
                        (vendor["id"], invoice_no, cents, due_date, po_number, idem_key or None))
        db.audit(c, "bill_created", f"#{cur.lastrowid} {vendor['name']} {invoice_no} {cents}")
        if (lost := int(db.chaos(c, "lost_response"))) > 0:
            db.set_chaos(c, "lost_response", lost - 1)
            return PlainTextResponse("502 Bad Gateway", 502)  # committed on exit -- the nasty case
        return render("message", brand="AcmeBooks", title="Bill saved", back="/erp/bills",
                      msg=f"Bill #{cur.lastrowid} saved: {vendor['name']} {invoice_no} for ${cents / 100:,.2f}, due {due_date}.")


@app.get("/erp/vendors")
def erp_vendors(request: Request):
    if r := guard(request, "erp"):
        return r
    with db.tx() as c:
        return render("erp_vendors", brand="AcmeBooks", title="Vendors", rows=c.execute("select * from vendors").fetchall())


@app.get("/erp/vendors/{vid}")
def erp_vendor(request: Request, vid: int):
    if r := guard(request, "erp"):
        return r
    with db.tx() as c:
        v = c.execute("select * from vendors where id=?", (vid,)).fetchone()
    if not v:
        raise HTTPException(404)
    return render("erp_vendor", brand="AcmeBooks", title=f"Vendor: {v['name']}", v=v)


@app.post("/erp/vendors/{vid}/bank")
def erp_vendor_bank(request: Request, vid: int, bank_account: str = Form("")):
    if r := guard(request, "erp"):
        return r
    with db.tx() as c:
        v = c.execute("select * from vendors where id=?", (vid,)).fetchone()
        if not v or not bank_account.strip():
            raise HTTPException(422, "Vendor and bank account required")
        c.execute("update vendors set bank_account=? where id=?", (bank_account.strip(), vid))
        db.audit(c, "bank_changed", f"{v['name']}: {v['bank_account']} -> {bank_account.strip()}")
    return render("message", brand="AcmeBooks", title="Bank details updated", back=f"/erp/vendors/{vid}",
                  msg=f"Bank account for {v['name']} is now {bank_account.strip()}.")


@app.get("/erp/expenses")
def erp_expenses(request: Request):
    if r := guard(request, "erp"):
        return r
    with db.tx() as c:
        return render("erp_expenses", brand="AcmeBooks", title="Expense reports",
                      rows=c.execute("select * from expenses order by id").fetchall())


@app.post("/erp/expenses/{eid}/{action}")
def erp_expense_action(request: Request, eid: int, action: str):
    if r := guard(request, "erp"):
        return r
    status = {"approve": "approved", "escalate": "escalated"}.get(action)
    with db.tx() as c:
        e = c.execute("select * from expenses where id=?", (eid,)).fetchone()
        if not status or not e:
            raise HTTPException(404)
        if e["status"] != "pending":
            return render("message", 409, brand="AcmeBooks", title="Not pending", back="/erp/expenses",
                          error=f"Expense {eid} is already {e['status']}.")
        c.execute("update expenses set status=? where id=?", (status, eid))
        db.audit(c, f"expense_{status}", f"#{eid} {e['amount_cents']}")
    return render("message", brand="AcmeBooks", title="Expense updated", back="/erp/expenses",
                  msg=f"Expense {eid} ({e['employee']}) {status}.")


# ---------- read-only JSON API (system of record for tools + verifier) ----------
def _rows(sql, *args):
    with db.tx() as c:
        out = [dict(r) for r in c.execute(sql, args)]
    for r in out:
        if "amount_cents" in r:
            r["amount"] = r.pop("amount_cents") / 100
    return out


@app.get("/api/bills")
def api_bills(vendor: str = "", invoice_no: str = ""):
    return _rows(BILL_SQL + " where v.name like ? and b.invoice_no like ? order by b.id", f"%{vendor}%", f"%{invoice_no}%")


@app.get("/api/invoices")
def api_invoices(vendor: str = ""):
    return _rows(INVOICE_SQL + " where v.name like ? order by i.id", f"%{vendor}%")


@app.get("/api/vendors")
def api_vendors():
    return _rows("select * from vendors order by id")


@app.get("/api/expenses")
def api_expenses():
    return _rows("select * from expenses order by id")


@app.get("/api/audit")
def api_audit():
    return _rows("select * from audit order by id")


# ---------- test/eval controls (not exposed to the agent's tools) ----------
@app.post("/_admin/reset")
def admin_reset():
    db.reset()
    return {"ok": True}


@app.post("/_admin/chaos")
async def admin_chaos(request: Request):
    with db.tx() as c:
        for k, v in (await request.json()).items():
            db.set_chaos(c, k, v)
        return {r["k"]: r["v"] for r in c.execute("select * from chaos")}
