# ── Stage 1: Builder ──────────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /app

# Install build dependencies
COPY pyproject.toml requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Create non-root user
RUN useradd -ms /bin/bash pfuser

# Copy installed packages from builder
COPY --from=builder /root/.local /home/pfuser/.local

# Copy application code
COPY core/ ./core/
COPY channels/ ./channels/
COPY smart/ ./smart/
COPY console/ ./console/
COPY launcher.py main.py ./

# Ensure user packages are on PATH
ENV PATH="/home/pfuser/.local/bin:$PATH" \
    PYTHONPATH="/app"

# Data directory (mount as volume for persistence)
RUN mkdir -p /data && chown pfuser:pfuser /data
VOLUME /data

# Switch to non-root user
USER pfuser

# Default: run brain + all services (headless)
EXPOSE 8503
CMD ["python", "launcher.py", "--config", "/data/config.json"]
