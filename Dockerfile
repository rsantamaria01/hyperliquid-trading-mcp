# Multi-stage build: deps + project are installed with uv from the committed
# lockfile in a builder stage; the slim runtime ships only the resulting .venv
# (no uv, no build deps, no dev deps).

# ---- builder ----
FROM python:3.12-slim AS builder

# Pinned uv binary (bump deliberately for reproducibility).
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Layer 1 — dependencies only (cached unless uv.lock/pyproject change).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev --no-editable

# Layer 2 — install the project itself (non-editable, into .venv).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=sse \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000 \
    HYPERLIQUID_SETTINGS_PATH=/data/settings.json \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy the built virtualenv (and the project metadata) from the builder.
COPY --from=builder /app/.venv /app/.venv

# Settings persist in /data — back this with a named volume in compose.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# Run the console-script straight from the venv (no uv at runtime).
ENTRYPOINT ["hyperliquid-trading-mcp"]
