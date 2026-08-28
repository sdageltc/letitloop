# Dockerfile for LetItLoop Cloud CI Watchdog (Linux VM / Container Host)
FROM python:3.11-slim@sha256:69b1704ab9d7758bfd6db0f93792070d6a0cf5c47794157140a3224749f7b3c2

WORKDIR /app

# Install git, gh cli, and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

COPY . /app/

RUN pip install --no-cache-dir ruff pytest pydantic httpx

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "orchestrator.cli", "watchdog", "--repo", "sdageltc/letitloop", "--limit", "5"]
