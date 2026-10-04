FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    EMBED_SANDBOX=1

WORKDIR /app

# Heavy layer first and cached: dependencies (read from pyproject.toml) + Chromium. A code-only change skips it.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium

COPY worker ./worker
COPY sandbox ./sandbox
COPY tasks ./tasks

# One process: console + embedded sandbox, on $PORT (injected by the platform).
CMD ["python", "-m", "worker.console"]
