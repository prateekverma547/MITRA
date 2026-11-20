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
#
# The image mirrors the repository layout — /app/backend and /app/frontend —
# rather than flattening backend/ into /app. The app resolves the frontend
# directory relative to its own file, so a flattened image would find the UIs
# locally and 500 in production.

# -- Frontend: build the console (served at /console) -------------------------
# The console is the product's one UI, the candidate's interview room (#/meet)
# included. Node only builds; nothing from this stage runs in production except
# the static files it produces. Track I's candidate app and frontend/ui are not
# built here: they are Track I's test apps, and nothing in the product links to
# /interview (which answers "not built" without them).
FROM node:22.23.2-slim AS console

WORKDIR /app/frontend/console
COPY frontend/console/package.json frontend/console/package-lock.json ./
RUN npm ci
COPY frontend/console/ ./
RUN npm run build

# -- Backend -------------------------------------------------------------------
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

WORKDIR /app/backend

# Dependencies first, so a code change does not re-resolve the whole tree.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The after-call speaker model (backend/aftercall/models.py: 26.5 MB, pinned by
# size and SHA-256). Fetched in its own layer from only the files that say what
# to fetch, so a code change does not download it again; `fetch` refuses a file
# whose checksum differs and `check` verifies it and the VAD model again. A
# local copy is kept out of the build context (.dockerignore), so only a
# verified download is ever in the image.
COPY backend/shared/ ./shared/
COPY backend/aftercall/__init__.py backend/aftercall/settings.py backend/aftercall/models.py ./aftercall/
RUN python -m aftercall.models fetch && python -m aftercall.models check

COPY backend/ ./
# The old join page and admin panel, served by FastAPI until cutover.
COPY frontend/ /app/frontend/
# The built console, served at /console (app/main.py).
COPY --from=console /app/frontend/console/dist /app/frontend/console/dist
RUN uv sync --frozen --no-dev

# Long-lived bot subprocesses must not be killed by an impatient shutdown: a
# 40-minute interview needs to finish writing its transcript.
STOPSIGNAL SIGINT

EXPOSE 8000

# $PORT is supplied by Railway; 8000 is the local default.
# --timeout-graceful-shutdown: uvicorn waits for open connections before it
# stops, and a live-view event stream stays open for the whole interview, so a
# deploy would otherwise wait until every watched interview ended (B-52). After
# 5 s the streams are closed; viewers reconnect and resume by sequence number,
# so nothing is lost. Bots are child processes watched by background tasks, not
# requests, so this neither waits for them nor ends them.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-graceful-shutdown 5"]
