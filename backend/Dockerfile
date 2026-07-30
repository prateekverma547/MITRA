# Interviewer backend: FastAPI plus the bot processes it spawns.
#
# One image, two roles. The web process serves the employer API and the
# candidate join page; it spawns `python -m bot.run_bot` as a child process per
# interview. Both need the same dependencies, so they share an image.
#
# A Dockerfile rather than Railway's auto-detection, because the build needs
# uv and a pinned Python, and because `ffmpeg`/`libsndfile` are system packages
# the audio stack needs and auto-detection will not install.
#
# NOTE ON PATHS: the build context is the REPOSITORY ROOT, not `backend/`, so
# every COPY below is written relative to the root. Writing them relative to
# this file's directory fails immediately with `"/uv.lock": not found`.

FROM python:3.12-slim AS base

# Python 3.12 specifically, not 3.13: pipecat 1.6.0 still imports the stdlib
# `audioop` module, which 3.13 removed.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a code change does not re-resolve the whole tree.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ .
RUN uv sync --frozen --no-dev

# Long-lived bot subprocesses must not be killed by an impatient shutdown: a
# 40-minute interview needs to finish writing its transcript.
STOPSIGNAL SIGINT

EXPOSE 8000

# $PORT is supplied by Railway; 8000 is the local default.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
