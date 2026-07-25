# Squirrel MCP server image.
#
# Defaults to the streamable-http transport (what you want in a container);
# for stdio, override the command. Configure via env vars / an --env-file.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SQUIRREL_MCP_TRANSPORT=streamable-http \
    SQUIRREL_MCP_HOST=0.0.0.0 \
    SQUIRREL_MCP_PORT=8000

WORKDIR /app

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml README.md ./
COPY squirrel_mcp ./squirrel_mcp
RUN pip install .

# Run as a non-root user.
RUN useradd --system --uid 10001 squirrel
USER squirrel

EXPOSE 8000

# Basic liveness: the MCP endpoint answers POSTs. A 4xx still proves the app is up.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; \
req=urllib.request.Request('http://127.0.0.1:8000/mcp', method='GET'); \
sys.exit(0) if urllib.request.urlopen(req, timeout=3) else sys.exit(1)" || exit 1

CMD ["python", "-m", "squirrel_mcp"]
