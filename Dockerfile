FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install project (pyproject.toml + src/) into the image
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install .

# MCP servers communicate over stdio — keep stdin open
ENTRYPOINT ["hyperliquid-trading-mcp"]
