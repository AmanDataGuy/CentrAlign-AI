"""Read-only connector to the ERP's JSON API. Lets the agent check state (duplicates, results) cheaply."""
import json
from typing import Annotated, Literal

import httpx
from pydantic import Field

from ..tools import ToolError, tool


@tool(untrusted=True)  # records hold vendor/employee-supplied free text
def erp_lookup(ctx, resource: Literal["bills", "vendors", "expenses"],
               vendor: Annotated[str, Field(description="Optional vendor name filter (bills)")] = "",
               invoice_no: Annotated[str, Field(description="Optional invoice number filter (bills)")] = ""):
    """Query AcmeBooks records (read-only). Use it to check for existing bills before entering one, and to confirm results."""
    try:
        r = httpx.get(f"{ctx.sandbox_url}/api/{resource}", params={"vendor": vendor, "invoice_no": invoice_no}, timeout=10)
    except httpx.HTTPError as e:
        raise ToolError("transient", f"ERP API unreachable: {e}")
    if r.status_code >= 500:
        raise ToolError("transient", f"ERP API HTTP {r.status_code}")
    rows = r.json()
    return json.dumps(rows[:50], indent=0) + (f"\n({len(rows)} rows, first 50 shown)" if len(rows) > 50 else "")
