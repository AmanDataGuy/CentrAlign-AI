"""Independent verifier. Reads the system of record; never trusts the agent's claim or memory.

A TaskSpec declares:
  truth:  {name: {api, where, pick: "max:field"|"min:field"}}  -> ground-truth rows from the SOURCE system
  checks: [{name, api, where, count | expect | each}]         -> postconditions on the TARGET system
Values may reference params ("{vendor}") or truth ("{invoice.amount}"). If the truth is ambiguous
(a tie), the checks pass if ANY candidate satisfies them -- the agent should have asked which one.
"""
import fnmatch
import itertools
import re
from dataclasses import dataclass, field

import httpx

PLACEHOLDER = re.compile(r"\{([\w.]+)\}")


@dataclass
class Verdict:
    ok: bool
    checks: list[dict] = field(default_factory=list)
    truth: dict = field(default_factory=dict)

    def summary(self) -> str:
        return "\n".join(f"{'PASS' if c['ok'] else 'FAIL'}  {c['name']}: {c['detail']}" for c in self.checks)


def _sub(v, env):
    if not isinstance(v, str):
        return v
    if m := PLACEHOLDER.fullmatch(v):  # whole value is one placeholder -> keep the typed value
        return _lookup(m.group(1), env)
    return PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1), env)), v)


def _lookup(path, env):
    cur = env
    for part in path.split("."):
        cur = cur[part]
    return cur


def _same(actual, expected) -> bool:
    if isinstance(expected, str) and (m := re.fullmatch(r"(<=|>=|<|>|!=)\s*(-?[\d.]+)", expected)):
        a, b = float(actual), float(m.group(2))
        return {"<=": a <= b, ">=": a >= b, "<": a < b, ">": a > b, "!=": a != b}[m.group(1)]
    if isinstance(expected, str) and "*" in expected:
        return fnmatch.fnmatchcase(str(actual), expected)
    try:
        return abs(float(actual) - float(expected)) < 0.005
    except (TypeError, ValueError):
        return str(actual).strip().lower() == str(expected).strip().lower()


def _rows(http, api, where, env):
    where = {k: _sub(v, env) for k, v in (where or {}).items()}
    return [r for r in http.get(api).raise_for_status().json() if all(_same(r.get(k), v) for k, v in where.items())]


def _check(http, chk, env) -> tuple[bool, str, list]:
    rows = _rows(http, chk["api"], chk.get("where"), env)
    if "count" in chk and len(rows) != chk["count"]:
        return False, f"expected {chk['count']} matching record(s), found {len(rows)}", rows
    if "expect" in chk:
        if not rows:
            return False, "no matching record", rows
        exp = {k: _sub(v, env) for k, v in chk["expect"].items()}
        bad = [f"{k}: got {r.get(k)!r}, expected {v!r}" for r in rows for k, v in exp.items() if not _same(r.get(k), v)]
        if bad:
            return False, "; ".join(bad), rows
    if "each" in chk:
        bad = [f"#{r.get('id')} {k}={r.get(k)!r} violates {v}" for r in rows for k, v in chk["each"].items()
               if not _same(r.get(k), _sub(v, env))]
        if bad:
            return False, "; ".join(bad), rows
    return True, f"{len(rows)} record(s) satisfy", rows


def verify(spec, base_url: str) -> Verdict:
    with httpx.Client(base_url=base_url, timeout=10) as http:
        candidates = {}
        for name, q in spec.truth.items():
            rows = _rows(http, q["api"], q.get("where"), dict(spec.params))
            if pick := q.get("pick"):
                op, fld = pick.split(":")
                if rows:
                    best = (max if op == "max" else min)(r[fld] for r in rows)
                    rows = [r for r in rows if r[fld] == best]
            if not rows:
                return Verdict(False, [{"name": f"truth:{name}", "ok": False, "detail": "ground truth not found"}])
            candidates[name] = rows

        best = None
        for combo in itertools.product(*candidates.values()) if candidates else [()]:
            env = {**spec.params, **dict(zip(candidates, combo))}
            results = []
            for chk in spec.checks:
                ok, detail, rows = _check(http, chk, env)
                results.append({"name": chk.get("name", chk["api"]), "ok": ok, "detail": detail, "evidence": rows[:3]})
            v = Verdict(all(r["ok"] for r in results), results, dict(zip(candidates, combo)))
            if v.ok:
                return v
            best = best or v
        return best
