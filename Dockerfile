# Build stage: lightweight python base
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.py README.md ./
COPY orchestrator ./orchestrator

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Runtime stage: secure non-root container
FROM python:3.11-slim AS runtime

WORKDIR /workspace

# Create least-privilege user
RUN useradd -m -u 1000 letitloop && \
    mkdir -p /workspace/scratch && \
    chown -R letitloop:letitloop /workspace

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/lil /usr/local/bin/letitloop /usr/local/bin/letitloop-mcp /usr/local/bin/

USER letitloop

ENV PYTHONUNBUFFERED=1
ENV LIL_WORKSPACE_ROOT=/workspace

ENTRYPOINT ["letitloop"]
CMD ["--help"]
