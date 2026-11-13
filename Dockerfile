FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv

ENV VENDOR_DIR=/app/vendor \
    TAU2_DATA_DIR=/app/vendor/tau2-bench/data \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1
WORKDIR /app

# Benchmarks first (big, rarely changes) so code edits don't refetch ~1 GB.
COPY scripts ./scripts
RUN uv venv /opt/venv && bash scripts/setup_benchmarks.sh --no-tau-install \
 && uv pip install -e /app/vendor/tau2-bench

COPY pyproject.toml README.md ./
COPY jevbench ./jevbench
RUN uv pip install -e .
COPY config ./config

EXPOSE 8000
CMD ["python", "-m", "jevbench", "dashboard", "--port", "8000"]
