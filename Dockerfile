# syntax=docker/dockerfile:1
# One image, two processes: the API (default CMD) and the Streamlit UI (override CMD).

# ---- build: install locked dependencies into /app/.venv ----
    FROM python:3.13-slim AS build
    COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
    ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
    WORKDIR /app
    # Dependencies first: this layer is cached until uv.lock / pyproject.toml change
    COPY pyproject.toml uv.lock ./
    RUN uv sync --frozen --no-dev --no-install-project
    # Then our code (changes often, so it comes last)
    COPY src ./src
    COPY README.md ./
    RUN uv sync --frozen --no-dev
    
    # ---- runtime: no uv, no compilers, non-root ----
    FROM python:3.13-slim
    RUN useradd --create-home --uid 10001 app
    WORKDIR /app
    COPY --from=build /app/.venv /app/.venv
    COPY --from=build /app/src /app/src
    COPY ui ./ui
    COPY scripts ./scripts
    COPY scenarios ./scenarios
    COPY data/seed ./data/seed
    RUN mkdir /data && chown app:app /data
    ENV PATH="/app/.venv/bin:$PATH" \
        PYTHONUNBUFFERED=1 \
        APP_DB_PATH=/data/app.db \
        CHECKPOINT_DB_PATH=/data/checkpoints.db
    USER app
    EXPOSE 8000 8501
    HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
      CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
    CMD ["uvicorn", "setuhaul.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
         "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]