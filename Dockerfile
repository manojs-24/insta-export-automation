# syntax=docker/dockerfile:1
#
# Web Automation Engine: the web page, the scheduler and the Chrome automation in one image.
#
#   docker compose up -d --build        (see docker-compose.yml and the README)
#
# Google Chrome only exists for amd64 on Linux, so this image is amd64-only.

# ---------------------------------------------------------------- build: dependencies + project
FROM python:3.14-slim-bookworm AS build

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir uv==0.12.23

WORKDIR /src

# Dependencies first, so editing the code does not reinstall them. --frozen = exactly uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable

# Then the project itself, installed as a normal (non-editable) package.
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


# ------------------------------------------------------------------------------------ runtime
FROM python:3.14-slim-bookworm AS runtime

# Fail early and clearly on an ARM build (Apple Silicon, Raspberry Pi, Graviton).
RUN [ "$(dpkg --print-architecture)" = "amd64" ] \
 || { echo "ERROR: Google Chrome for Linux is amd64-only. Build with: --platform linux/amd64" >&2; exit 1; }

# Chrome (the same .deb Playwright's own installer uses), a virtual display for the headed
# window, and a tiny init so Chrome's helper processes are reaped. Chrome's dependencies come
# in through apt. NOTE: "stable_current" is whatever Google ships when the image is built.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl fonts-liberation tini xvfb \
 && curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -y --no-install-recommends /tmp/chrome.deb \
 && rm -f /tmp/chrome.deb \
 && rm -rf /var/lib/apt/lists/* \
 && command -v tini \
 && command -v Xvfb \
 && google-chrome-stable --version

# An unprivileged user. /tmp/.X11-unix must exist, owned by root and sticky, for Xvfb.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app \
 && mkdir -p /app /data/browser_data /data/exports /tmp/.X11-unix \
 && chmod 1777 /tmp/.X11-unix \
 && chown -R app:app /app /data

COPY --from=build /opt/venv /opt/venv
COPY --chown=app:app configs /app/configs
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# (strips Windows line endings if the file was checked out with them; they break the shebang)
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod 0755 /usr/local/bin/entrypoint.sh

# Data lives under /data (the two volumes below), whatever .env says about relative folders.
# Reaching the page from outside the container needs WA_UI_USERNAME and WA_UI_PASSWORD:
# the app refuses to listen on 0.0.0.0 without a sign-in.
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    TZ=UTC \
    DISPLAY=:99 \
    WA_PROFILE_ROOT=/data/browser_data \
    WA_EXPORT_DIR=/data/exports \
    WA_UI_HOST=0.0.0.0

WORKDIR /app
USER app

# browser_data holds the saved Instagram login; exports holds the downloaded zip.
VOLUME ["/data/browser_data", "/data/exports"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/auth/mode', timeout=4).status == 200 else 1)"

ENTRYPOINT ["tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["web-automation", "--ui", "--port", "8000"]
