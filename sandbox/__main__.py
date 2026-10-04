"""python -m sandbox [--port 8001] -- fresh seed every start. No --reload (breaks Playwright on Windows)."""
import argparse

import uvicorn

from . import db
from .app import app

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=8001)
args = p.parse_args()
db.reset()
uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
