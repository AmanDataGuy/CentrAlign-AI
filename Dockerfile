FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    EMBED_SANDBOX=1

WORKDIR /app

COPY pyproject.toml ./
COPY worker ./worker
COPY sandbox ./sandbox
COPY tasks ./tasks

# Dependencies come from pyproject.toml; the code itself runs from /app.
RUN pip install --no-cache-dir . && python -m playwright install --with-deps chromium

# One process: console + embedded sandbox, on $PORT (injected by the platform).
CMD ["python", "-m", "worker.console"]
