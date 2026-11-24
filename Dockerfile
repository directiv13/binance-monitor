# syntax=docker/dockerfile:1

# ---- build stage: resolve and install the app + pinned deps into a venv --------------------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# ---- runtime stage ------------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DB_PATH=/data/monitor.db \
    HEARTBEAT_PATH=/data/heartbeat

# Non-root user; /data is created and owned by it so a fresh named volume inherits the owner.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data /app \
    && chown app:app /data /app

COPY --from=builder /opt/venv /opt/venv

USER app
WORKDIR /app
VOLUME ["/data"]

STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-m", "binance_monitor.healthcheck"]

CMD ["python", "-m", "binance_monitor"]
