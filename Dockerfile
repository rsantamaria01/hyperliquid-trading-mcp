FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_TRANSPORT=sse \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000 \
    HYPERLIQUID_SETTINGS_PATH=/data/settings.json

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install .

# Settings persist in /data — back this with a named volume in compose
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

ENTRYPOINT ["hyperliquid-trading-mcp"]
