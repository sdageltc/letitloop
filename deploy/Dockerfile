# Dockerfile for LetItLoop Cloud CI Watchdog (Azure B1s / Oracle Ampere A1)
FROM python:3.11-slim

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
