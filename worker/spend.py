"""Spend guard. Every paid LLM call is priced from the provider's reported usage and recorded in a ledger shared by
all runs (CLI, console, evals). A call is refused once the ledger reaches the budget. Unknown model -> refuse.

ponytail: checked before each call, so one in-flight call can overshoot by its own cost (~$0.01-0.03 here);
that is what the margin under the hard cap is for.
"""
import json
import os
import threading
from pathlib import Path

from .models import atomic_write

# USD per 1M tokens: (input, output incl. thinking, cached input). Source: ai.google.dev/gemini-api/docs/pricing, 2026-10-04.
PRICES = {
    "gemini-3.8-flash": (0.75, 3.75, 0.075), "gemini-3.7-flash": (0.75, 3.75, 0.075), "gemini-3.6-flash": (0.75, 3.75, 0.075),
    "gemini-3.5-flash": (1.50, 9.00, 0.15), "gemini-3.5-flash-lite": (0.30, 2.50, 0.03), "gemini-3.1-flash-lite": (0.25, 1.50, 0.025),
    "gemini-2.5-flash": (0.30, 2.50, 0.03), "gemini-2.5-flash-lite": (0.10, 0.40, 0.01),
}
_lock = threading.Lock()


class BudgetExceeded(Exception):
    pass


class Ledger:
    def __init__(self, path: Path | None = None, budget: float | None = None):
        self.path = path or Path(os.environ.get("WORKER_LEDGER", "runs/spend.json"))
        self.budget = float(budget if budget is not None else os.environ.get("WORKER_BUDGET_USD", "1.80"))

    def _load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {"total_usd": 0.0, "by_run": {}}

    def total(self) -> float:
        return self._load()["total_usd"]

    @staticmethod
    def price(model: str) -> tuple[float, float, float]:
        if model in PRICES:
            return PRICES[model]
        if os.environ.get("WORKER_PRICE_IN") and os.environ.get("WORKER_PRICE_OUT"):
            p_in = float(os.environ["WORKER_PRICE_IN"])
            return p_in, float(os.environ["WORKER_PRICE_OUT"]), p_in  # no cache discount assumed
        raise BudgetExceeded(f"No known price for model {model!r}: refusing to spend blind. "
                             "Use a priced model or set WORKER_PRICE_IN / WORKER_PRICE_OUT (USD per 1M tokens).")

    def check(self, model: str):
        self.price(model)
        if (t := self.total()) >= self.budget:
            raise BudgetExceeded(f"Spend guard: ${t:.4f} spent, budget is ${self.budget:.2f}. No further LLM calls.")

    def record(self, model: str, usage: dict, run_id: str) -> float:
        p_in, p_out, p_cached = self.price(model)
        cached = min(usage.get("cached", 0), usage["in"])  # implicit-cache hits are billed at the cache rate
        usd = ((usage["in"] - cached) * p_in + cached * p_cached + usage["out"] * p_out) / 1e6
        with _lock:
            data = self._load()
            data["total_usd"] = round(data["total_usd"] + usd, 6)
            data["by_run"][run_id] = round(data["by_run"].get(run_id, 0.0) + usd, 6)
            atomic_write(self.path, json.dumps(data, indent=1))
        return usd


if __name__ == "__main__":  # python -m worker.spend  -> show the ledger
    led = Ledger()
    print(f"spent ${led.total():.4f} of ${led.budget:.2f} budget")
