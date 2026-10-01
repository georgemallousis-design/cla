# autoshorts in a container: FFmpeg (libass, libx264), espeak-ng, fonts and Python.
#
#   docker build -t autoshorts .
#   mkdir data
#   docker run --rm -v "$PWD/data:/app/data" autoshorts init     # config.yaml, .env, topics.txt
#   docker run --rm -v "$PWD/data:/app/data" autoshorts doctor
#   docker run --rm -v "$PWD/data:/app/data" autoshorts make
#
# Everything you edit or produce lives in the mounted /app/data folder (config.yaml, .env,
# topics.txt, assets/, secrets/, output/, cache/, state/). See README.md, section "Docker".
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XDG_CACHE_HOME=/tmp/.cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg espeak-ng fonts-dejavu-core fonts-liberation fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source and templates. Installed in editable mode so `autoshorts init` finds
# config.example.yaml / .env.example / topics.example.txt next to the package and the
# built-in data files (autoshorts/data/) are used straight from the source tree.
WORKDIR /app/src
COPY pyproject.toml README.md LICENSE config.example.yaml .env.example topics.example.txt ./
COPY autoshorts ./autoshorts
RUN pip install -e ".[youtube]"

# Run as an unprivileged user (uid 1000 matches the first user on most Linux hosts;
# pass --user "$(id -u):$(id -g)" to docker run if yours differs).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin autoshorts \
    && mkdir -p /app/data \
    && chown autoshorts:autoshorts /app/data
USER autoshorts

WORKDIR /app/data
VOLUME ["/app/data"]

ENTRYPOINT ["autoshorts"]
CMD ["--help"]
