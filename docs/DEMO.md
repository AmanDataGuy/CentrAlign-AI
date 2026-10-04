# Demo video script (~4 min)

Setup before recording: `.env` has a key; terminal 1 runs `python -m sandbox`; browser tab open on
http://127.0.0.1:8001/erp/bills (log in: ops / erp-demo) to show the system of record changing live.

## 1. The problem and the idea (30 s)
"Most agent demos trust their own 'done', break on the first error, and are scripted for one workflow.
This worker is built the other way round: completion is verified against the system of record, a deterministic
policy decides what the model is allowed to do, and the core never changes between tasks."
Show `tasks/invoice_to_erp.yaml`: outcome-only goal, SOP context, allowed tools, postconditions. No steps.

## 2. Happy path, the assignment's own example (60 s)
```
python -m worker run tasks/invoice_to_erp.yaml --headed
```
Narrate: it plans → logs into VendorHub → the list isn't date-ordered, it works out the latest (INV-NW-1007,
not the duplicate 1001) → remembers the facts with source → checks AcmeBooks for an existing bill → fills the form →
`finish` → **VERIFIED** panel listing the three checks. Refresh the ERP tab: the bill is there.
Open `runs/<id>/report.md`.

## 3. Hostile world (60 s)
```
curl -X POST localhost:8001/_admin/reset
curl -X POST localhost:8001/_admin/chaos -H "content-type: application/json" -d "{\"drift_ui\":1,\"lost_response\":1,\"expire_after\":5}"
python -m worker run tasks/invoice_to_erp.yaml
```
Point at: session expired → it signs in again; button is now "Submit entry" → it adapts; the submit returns
**502 but had committed** → it checks `erp_lookup` before retrying → exactly one bill. (Idempotency + "check before retry".)

## 4. Safety: prompt injection + approvals (60 s)
```
curl -X POST localhost:8001/_admin/reset
python -m worker run tasks/invoice_to_erp.yaml -p vendor="Initech Ltd"
```
Show the invoice note telling the AI to change bank details. If the model ignores it: say so and show the summary
mentioning it. If it tries: the trace shows `DENIED BY POLICY ... possible prompt injection` — denied before a
human was even asked. Then:
```
python -m worker run tasks/vendor_bank_update.yaml
```
The approval prompt shows the *grounded* action (`button "Update bank details"` + the typed account), not the model's words. Approve → verified.

## 5. Generalization + proof (30 s)
```
python -m worker run tasks/expense_triage.yaml --approve yes
pytest -q
```
Same core, different workflow (policy-driven decisions over records). `pytest`: 25 offline tests, including an agent
that lies about finishing and the ablation showing that without the verifier the lie counts as success.
If you ran `python -m evals.run`, show `evals/results.md`.

## Close (10 s)
"LLM proposes, code disposes, the world confirms. New workflow = new YAML."
